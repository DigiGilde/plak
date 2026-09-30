"""Rate limiting per endpoint class: fixed window, no escalating
penalties, with a global backstop limit per class.

The middleware is a pure ASGI class; `make_rate_limit_middleware()` returns
`(middleware_class, kwargs)` for `app.add_middleware(klass, **kwargs)`.
"""

from __future__ import annotations

import enum
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from plak import i18n, messages, net
from plak.config import Settings
from plak.constants import (
    PATH_CONTENT_CODE,
    PATH_CONTENT_LOGIN,
    PATH_CONTENT_OAUTH2_PREFIX,
    PATH_LOGIN,
    PATH_LOGOUT,
    PATH_OAUTH2_PREFIX,
)
from plak.messages import Msg

_logger = logging.getLogger(__name__)

# Path prefixes that make up the body of the session API/login, the same
# spelling on both hosts (/-/...). Shared with
# platform/pages.py (see constants.py) so routes and rate-limit class cannot
# drift apart.
_LOGIN_PREFIXES = (
    PATH_LOGIN,
    PATH_OAUTH2_PREFIX,
    PATH_LOGOUT,
    PATH_CONTENT_LOGIN,
    PATH_CONTENT_OAUTH2_PREFIX,
)
# The API class covers the API alone. The SPA sits outside this middleware
# (main.py) and on the root of the admin host its paths are indistinguishable
# from content paths anyway: whatever the SPA does not answer there is an app
# path or a miss, and the content class is the right bucket for those. This
# module sits below the API layer, so this is a literal rather than an import
# from api/errors.py.
_API_PREFIXES = ("/-/api",)
_EXEMPT_PATHS = frozenset({"/healthz"})


_CLEANUP_INTERVAL = 128
_DEFAULT_MAX_KEYS = 50_000

_BACKSTOP_KEY = "__backstop__"


class RateLimitClass(enum.StrEnum):
    LOGIN = "login"
    API = "api"
    CONTENT = "content"
    CODE = "code"


def class_for_path(path: str) -> RateLimitClass | None:
    """Determines the rate-limit class for a path; None means exempt."""
    if path in _EXEMPT_PATHS:
        return None
    # Its own class, and the strictest of the four: guessing a code here is
    # the one place on the content host where guessing gets you in
    # (serving/code_page.py). The limit per selector sits in the route itself;
    # this one counts per IP.
    if path == PATH_CONTENT_CODE:
        return RateLimitClass.CODE
    if path.startswith(_LOGIN_PREFIXES):
        return RateLimitClass.LOGIN
    if path.startswith(_API_PREFIXES):
        return RateLimitClass.API
    return RateLimitClass.CONTENT


@dataclass(frozen=True)
class Limit:
    max: int
    window_s: int
    global_max: int


def limit_for(settings: Settings, klass: RateLimitClass) -> Limit:
    prefix = f"ratelimit_{klass.value}"
    return Limit(
        max=getattr(settings, f"{prefix}_max"),
        window_s=getattr(settings, f"{prefix}_window_s"),
        global_max=getattr(settings, f"{prefix}_global_max"),
    )


@dataclass
class _Bucket:
    window_start: float
    window_s: float
    count: int = 0


@dataclass
class CounterResult:
    count: int
    remaining_s: float


class InMemoryCounter:
    """Per-key counters with a fixed window, in the memory of this process.

    Bounded (`max_keys`) and cleaned up periodically so that expired
    windows of keys that never come back (e.g. one-off IPs) do not linger
    indefinitely.
    """

    def __init__(self, max_keys: int = _DEFAULT_MAX_KEYS) -> None:
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._max_keys = max_keys
        self._calls = 0

    async def increment(self, key: str, window_s: int, now_: float) -> CounterResult:
        # No await anywhere in this method: under asyncio's cooperative
        # concurrency another task can therefore never interleave halfway,
        # which makes this atomic without an explicit lock (also for
        # concurrent requests on the same key).
        self._calls += 1
        if self._calls % _CLEANUP_INTERVAL == 0:
            self.cleanup(now_)

        bucket = self._buckets.get(key)
        if bucket is None or (now_ - bucket.window_start) >= bucket.window_s:
            bucket = _Bucket(window_start=now_, window_s=window_s)
            self._buckets[key] = bucket
            # A new window only lands at the end of the insertion order after an
            # explicit move: assigning an existing key keeps its old position.
            # `_evict_oldest` reads that order as the order of `window_start`.
            self._buckets.move_to_end(key)
        bucket.count += 1

        if len(self._buckets) > self._max_keys:
            self._evict_oldest()

        remaining = bucket.window_s - (now_ - bucket.window_start)
        return CounterResult(count=bucket.count, remaining_s=max(remaining, 0.0))

    def cleanup(self, now_: float) -> int:
        """Removes buckets whose window has already passed. Returns the number of buckets removed."""
        expired = [key for key, bucket in self._buckets.items() if (now_ - bucket.window_start) >= bucket.window_s]
        for key in expired:
            del self._buckets[key]
        return len(expired)

    def _evict_oldest(self) -> None:
        self._buckets.popitem(last=False)

    def __len__(self) -> int:
        return len(self._buckets)


GetKeyFunction = Callable[[Request], Awaitable[str | None]]


class RateLimitMiddleware:
    """Pure ASGI middleware: counts per class and key, refuses with 429.

    `get_authenticated_key` yields the session sub to count on (main.py
    passes one in); without it, counting falls back to IP.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        get_authenticated_key: GetKeyFunction | None = None,
        counter: InMemoryCounter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._settings = settings
        self._get_authenticated_key = get_authenticated_key
        self._counter = counter if counter is not None else InMemoryCounter()
        # Apart from the per-key buckets, which the client chooses and can
        # therefore push past `max_keys`: the eviction that follows must never
        # reset a backstop.
        self._backstops = InMemoryCounter()
        self._clock = clock
        self._trusted_networks = net.parse_trusted_proxies(settings.trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        klass = class_for_path(request.url.path)
        if klass is None:
            await self._app(scope, receive, send)
            return

        limit = limit_for(self._settings, klass)
        now_ = self._clock()
        accept_language = request.headers.get("accept-language")

        try:
            key = await self._determine_key(request)
            per_key = await self._counter.increment(f"{klass.value}:{key}", limit.window_s, now_)
            if per_key.count > limit.max:
                # The backstop stays uncharged for a request that is already
                # refused: otherwise one key could spend the shared budget of
                # its whole class and lock every other client out.
                refusal_s: float | None = per_key.remaining_s
            else:
                backstop = await self._backstops.increment(f"{klass.value}:{_BACKSTOP_KEY}", limit.window_s, now_)
                refusal_s = backstop.remaining_s if backstop.count > limit.global_max else None
        except Exception:
            _logger.exception("Ratelimit-teller kapot; verzoek fail-closed geweigerd (klasse=%s)", klass.value)
            response = _too_many_requests_response(limit.window_s, accept_language)
            await response(scope, receive, send)
            return

        if refusal_s is not None:
            response = _too_many_requests_response(refusal_s, accept_language)
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)

    async def _determine_key(self, request: Request) -> str:
        # A bearer token is never a key here: the middleware cannot verify it,
        # and keying on an unverified value would let every made-up token open
        # a fresh budget. Bearer requests count per client IP.
        if self._get_authenticated_key is not None:
            member_key = await self._get_authenticated_key(request)
            if member_key:
                return f"member:{member_key}"

        return f"ip:{net.rate_limit_key(net.client_ip(request, self._trusted_networks))}"


def _too_many_requests_response(remaining_s: float, accept_language: str | None) -> JSONResponse:
    # A deliberately inline problem+json body instead of importing api.errors:
    # this module sits below the API layer and must not depend on it. The
    # catalogue it renders from does sit below both.
    retry_after = max(1, math.ceil(remaining_s))
    locale = i18n.negotiate(accept_language, default=i18n.API_DEFAULT)
    return JSONResponse(
        status_code=429,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": messages.title(locale, 429),
            "status": 429,
            "detail": messages.render(locale, Msg("TOO_MANY_REQUESTS")),
            "code": "TOO_MANY_REQUESTS",
        },
        headers={"Retry-After": str(retry_after)},
    )


def make_rate_limit_middleware(
    settings: Settings,
    *,
    get_authenticated_key: GetKeyFunction | None = None,
    counter: InMemoryCounter | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[type[RateLimitMiddleware], dict]:
    """Factory for the rate-limit middleware."""
    return RateLimitMiddleware, {
        "settings": settings,
        "get_authenticated_key": get_authenticated_key,
        "counter": counter if counter is not None else InMemoryCounter(),
        "clock": clock,
    }
