"""Tests for rate limiting (spec §10): class assignment, fixed window, the
global backstop, key derivation and the "normal clicking" scenario."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx
import pytest
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from plak.config import Settings
from plak.ratelimit import (
    InMemoryCounter,
    RateLimitClass,
    RateLimitMiddleware,
    class_for_path,
    limit_for,
    make_rate_limit_middleware,
)

# asyncio_mode = "auto" (pyproject.toml) picks up async def tests by itself; this
# file deliberately mixes sync and async tests, so no module-wide asyncio marker.


async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse("ok")(scope, receive, send)


def _make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "db_url": "postgresql+asyncpg://user:pass@localhost/db",
        "content_root": "var/plak-content",
        "oidc_issuer": "https://issuer.example.org",
        "oidc_client_id": "client-id",
        "oidc_client_private_jwk": "{}",
        "oidc_required_acr": "loa2",
        "session_secret": "s" * 32,
        "audit_pepper": "p" * 32,
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "trusted_proxies": "",
        "content_base_url": "https://plak.example",
    }
    base.update(overrides)
    return Settings(**base)


class _FakeClock:
    """Manually advanceable clock, so windows elapse under control."""

    def __init__(self, start: float = 0.0) -> None:
        self.now_ = start

    def __call__(self) -> float:
        return self.now_


def _make_client(
    settings: Settings,
    *,
    clock: Callable[[], float] = time.monotonic,
    counter: InMemoryCounter | None = None,
    get_key: Callable[[httpx.Request], Awaitable[str | None]] | None = None,
    client_ip: str = "203.0.113.9",
) -> httpx.AsyncClient:
    middleware = RateLimitMiddleware(
        app=_ok_app,
        settings=settings,
        get_authenticated_key=get_key,
        counter=counter if counter is not None else InMemoryCounter(),
        clock=clock,
    )
    transport = httpx.ASGITransport(app=middleware, client=(client_ip, 12345))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# --- Class assignment and configuration ------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/healthz", None),
        ("/-/login", RateLimitClass.LOGIN),
        ("/-/oauth2/callback", RateLimitClass.LOGIN),
        ("/-/logout", RateLimitClass.LOGIN),
        ("/-/api/v1/groups", RateLimitClass.API),
        ("/-/api/docs", RateLimitClass.API),
        # Handing in the code of a secret link: its own, strictest class.
        ("/-/code", RateLimitClass.CODE),
        # Since the SPA moved to the root of the admin host its paths are
        # spelled like content paths, and it answers them outside this
        # middleware anyway (main.py). What is left for the limiter is the
        # app's own miss and real content: both belong in the widest class.
        ("/assets/app-abc123.js", RateLimitClass.CONTENT),
        ("/-/onbekend", RateLimitClass.CONTENT),
        ("/admin/", RateLimitClass.CONTENT),
        ("/nldd/website/", RateLimitClass.CONTENT),
        ("/nldd/website/_preview/pr-1/", RateLimitClass.CONTENT),
        ("/", RateLimitClass.CONTENT),
    ],
)
def test_class__for_path(path: str, expected: RateLimitClass | None) -> None:
    assert class_for_path(path) == expected


def test_default_limits_conform_spec() -> None:
    settings = _make_settings()

    login = limit_for(settings, RateLimitClass.LOGIN)
    api = limit_for(settings, RateLimitClass.API)
    content = limit_for(settings, RateLimitClass.CONTENT)
    code = limit_for(settings, RateLimitClass.CODE)

    assert (login.max, login.window_s, login.global_max) == (10, 60, 1000)
    assert (api.max, api.window_s, api.global_max) == (60, 60, 5000)
    assert (content.max, content.window_s, content.global_max) == (600, 60, 20000)
    assert (code.max, code.window_s, code.global_max) == (10, 900, 500)


# --- InMemoryCounter: window logic, reset, bounding ---------------------------------------


async def test_counter_counts_on_inside_window() -> None:
    counter = InMemoryCounter()
    r1 = await counter.increment("key", 10, now_=0.0)
    r2 = await counter.increment("key", 10, now_=5.0)
    assert (r1.count, r2.count) == (1, 2)


async def test_counter_reset_after_elapsed_window() -> None:
    counter = InMemoryCounter()
    await counter.increment("key", 10, now_=0.0)
    await counter.increment("key", 10, now_=5.0)
    r3 = await counter.increment("key", 10, now_=10.0)
    assert r3.count == 1


async def test_counter_cleanup_deletes_expired_buckets() -> None:
    counter = InMemoryCounter()
    await counter.increment("oud", 10, now_=0.0)
    await counter.increment("new", 10, now_=100.0)

    deleted = counter.cleanup(now_=100.0)

    assert deleted == 1
    assert len(counter) == 1


async def test_counter_evict_oldest_bucket_on_overrun_max() -> None:
    counter = InMemoryCounter(max_keys=3)
    await counter.increment("a", 1000, now_=0.0)
    await counter.increment("b", 1000, now_=1.0)
    await counter.increment("c", 1000, now_=2.0)
    await counter.increment("d", 1000, now_=3.0)

    assert len(counter) == 3
    r = await counter.increment("a", 1000, now_=4.0)
    assert r.count == 1  # "a" was the oldest and got evicted, so a fresh bucket


async def test_counter_evicts_oldest_window_not_first_seen_key() -> None:
    counter = InMemoryCounter(max_keys=2)
    await counter.increment("a", 10, now_=0.0)
    await counter.increment("b", 10, now_=1.0)
    # "a" opens a fresh window and is from now on the youngest bucket, even
    # though it was the first key seen.
    await counter.increment("a", 10, now_=20.0)
    await counter.increment("c", 10, now_=21.0)

    assert (await counter.increment("a", 10, now_=22.0)).count == 2
    assert (await counter.increment("b", 10, now_=22.0)).count == 1


# --- Middleware: window behaviour, reset, backstop, 429 + Retry-After -------------------


async def test_inside_limit_gives_200() -> None:
    settings = _make_settings(ratelimit_login_max=2, ratelimit_login_window_s=10)
    async with _make_client(settings) as client:
        for _ in range(2):
            resp = await client.get("/-/login")
            assert resp.status_code == 200


async def test_above_limit_gives_429_with_retry_after() -> None:
    settings = _make_settings(ratelimit_login_max=2, ratelimit_login_window_s=10)
    clock = _FakeClock()
    async with _make_client(settings, clock=clock) as client:
        for _ in range(2):
            assert (await client.get("/-/login")).status_code == 200
        resp = await client.get("/-/login")

    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "10"
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["status"] == 429
    assert body["code"] == "TOO_MANY_REQUESTS"
    # English, because the request asks for no language of its own.
    assert "Too many requests" in body["detail"]


async def test_the_429_follows_accept_language() -> None:
    settings = _make_settings(ratelimit_login_max=1, ratelimit_login_window_s=10)
    async with _make_client(settings, clock=_FakeClock()) as client:
        assert (await client.get("/-/login")).status_code == 200
        resp = await client.get("/-/login", headers={"Accept-Language": "nl"})

    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == "TOO_MANY_REQUESTS"
    assert "Te veel verzoeken" in body["detail"]
    assert body["title"] == "Te veel verzoeken"


async def test_content_login_falls_under_login_limit_not_content() -> None:
    """The login on the content host (/-/login, /-/oauth2/callback) counts
    in the LOGIN class, not in the far roomier CONTENT class."""
    settings = _make_settings(
        ratelimit_login_max=1,
        ratelimit_login_window_s=60,
        ratelimit_content_max=1000,
        ratelimit_content_window_s=60,
    )
    async with _make_client(settings) as client:
        r1 = await client.get("/-/login")
        r2 = await client.get("/-/oauth2/callback?code=x&state=y")
        r3 = await client.get("/nldd/website/")

    # login and callback share the login budget (1); content has its own, roomy budget.
    assert [r1.status_code, r2.status_code] == [200, 429]
    assert r3.status_code == 200


async def test_window_reset_after_expired_via_middleware() -> None:
    settings = _make_settings(ratelimit_login_max=1, ratelimit_login_window_s=5)
    clock = _FakeClock()
    async with _make_client(settings, clock=clock) as client:
        assert (await client.get("/-/login")).status_code == 200
        assert (await client.get("/-/login")).status_code == 429

        clock.now_ = 5.0  # window elapsed

        assert (await client.get("/-/login")).status_code == 200


async def test_global_backstop_stores_to_about_all_keys() -> None:
    settings = _make_settings(
        ratelimit_api_max=1000,
        ratelimit_api_window_s=60,
        ratelimit_api_global_max=3,
        trusted_proxies="127.0.0.1/32",
    )
    async with _make_client(settings, client_ip="127.0.0.1") as client:
        statuses = []
        for i in range(4):
            resp = await client.get(
                "/-/api/v1/x", headers={"x-forwarded-for": f"198.51.100.{i}"}
            )
            statuses.append(resp.status_code)

    # every request comes from a different (trusted-XFF) key, so the per-key
    # budget (1000) is never reached: only the backstop (3) fires.
    assert statuses == [200, 200, 200, 429]


async def test_refused_key_does_not_spend_the_global_backstop() -> None:
    """One key that runs into its own limit may not exhaust the shared
    backstop, which would refuse every other client for the rest of the
    window."""
    settings = _make_settings(
        ratelimit_api_max=2,
        ratelimit_api_window_s=60,
        ratelimit_api_global_max=5,
        trusted_proxies="127.0.0.1/32",
    )
    async with _make_client(settings, clock=_FakeClock(), client_ip="127.0.0.1") as client:
        attacker = [
            (
                await client.get("/-/api/v1/x", headers={"x-forwarded-for": "203.0.113.9"})
            ).status_code
            for _ in range(10)
        ]
        other = await client.get("/-/api/v1/x", headers={"x-forwarded-for": "198.51.100.7"})

    assert attacker == [200, 200] + [429] * 8
    assert other.status_code == 200


async def test_authenticated_counted_per_member_not_per_ip() -> None:
    settings = _make_settings(
        ratelimit_api_max=2, ratelimit_api_window_s=60, ratelimit_api_global_max=1000
    )

    async def get_key(request: httpx.Request) -> str | None:
        return request.headers.get("x-test-lid-sub")

    async with _make_client(settings, get_key=get_key) as client:
        r1 = await client.get(
            "/-/api/v1/x", headers={"x-test-lid-sub": "lid-1", "x-forwarded-for": "1.2.3.4"}
        )
        r2 = await client.get(
            "/-/api/v1/x", headers={"x-test-lid-sub": "lid-1", "x-forwarded-for": "5.6.7.8"}
        )
        r3 = await client.get("/-/api/v1/x", headers={"x-test-lid-sub": "lid-1"})
        r4 = await client.get("/-/api/v1/x", headers={"x-test-lid-sub": "lid-2"})

    # same lid, changing (untrusted) IPs: still one shared budget of 2.
    assert [r1.status_code, r2.status_code, r3.status_code] == [200, 200, 429]
    # another lid has a budget of its own, still unused.
    assert r4.status_code == 200


async def test_made_up_cli_tokens_do_not_open_fresh_budgets() -> None:
    """Keying on an unverified selector would give every invented token its
    own budget; bearer requests count per client IP instead."""
    settings = _make_settings(ratelimit_api_max=1, ratelimit_api_window_s=60)
    async with _make_client(settings) as client:
        r1 = await client.get("/-/api/v1/x", headers={"authorization": "Bearer plakcli_0123456789abcdef_" + "a" * 64})
        r2 = await client.get("/-/api/v1/x", headers={"authorization": "Bearer plakcli_fedcba9876543210_" + "b" * 64})

    assert [r1.status_code, r2.status_code] == [200, 429]


async def test_a_ci_id_token_counts_per_ip() -> None:
    settings = _make_settings(ratelimit_api_max=1, ratelimit_api_window_s=60)
    async with _make_client(settings) as client:
        r1 = await client.get("/-/api/v1/x", headers={"authorization": "Bearer aaa.bbb.ccc"})
        r2 = await client.get("/-/api/v1/x", headers={"authorization": "Bearer ddd.eee.fff"})

    assert [r1.status_code, r2.status_code] == [200, 429]


async def test_xff_only_honoured_from_trusted_proxy() -> None:
    settings = _make_settings(
        ratelimit_content_max=1, ratelimit_content_window_s=60, trusted_proxies="10.0.0.1/32"
    )

    # untrusted remote: XFF is ignored, every client shares the remote's budget.
    async with _make_client(settings, client_ip="203.0.113.99") as client:
        r1 = await client.get("/nldd/website/", headers={"x-forwarded-for": "1.1.1.1"})
        r2 = await client.get("/nldd/website/", headers={"x-forwarded-for": "2.2.2.2"})
    assert [r1.status_code, r2.status_code] == [200, 429]

    # trusted remote: XFF decides the key, so every client gets its own budget.
    async with _make_client(settings, client_ip="10.0.0.1") as client:
        r3 = await client.get("/nldd/website/", headers={"x-forwarded-for": "1.1.1.1"})
        r4 = await client.get("/nldd/website/", headers={"x-forwarded-for": "2.2.2.2"})
    assert [r3.status_code, r4.status_code] == [200, 200]


async def test_spoofed_leftmost_xff_does_not_become_the_key() -> None:
    """The leftmost XFF entry is one the client can set itself; the key has to
    be the rightmost untrusted address (rightmost-after-trusted)."""
    settings = _make_settings(
        ratelimit_content_max=1, ratelimit_content_window_s=60, trusted_proxies="10.0.0.1/32"
    )
    async with _make_client(settings, client_ip="10.0.0.1") as client:
        r1 = await client.get(
            "/nldd/website/", headers={"x-forwarded-for": "6.6.6.6, 203.0.113.7"}
        )
        # same real client (203.0.113.7), a different spoofed leftmost value:
        # must count on the same key and therefore give a 429.
        r2 = await client.get(
            "/nldd/website/", headers={"x-forwarded-for": "7.7.7.7, 203.0.113.7"}
        )
    assert [r1.status_code, r2.status_code] == [200, 429]


async def test_rotating_leftmost_xff_does_not_evade_the_limit() -> None:
    settings = _make_settings(
        ratelimit_content_max=2, ratelimit_content_window_s=60, trusted_proxies="10.0.0.1/32"
    )
    async with _make_client(settings, client_ip="10.0.0.1") as client:
        statuses = []
        for i in range(4):
            resp = await client.get(
                "/nldd/website/", headers={"x-forwarded-for": f"1.1.1.{i}, 203.0.113.7"}
            )
            statuses.append(resp.status_code)
    assert statuses == [200, 200, 429, 429]


async def test_xff_with_only_trusted_hops_falls_back_to_peer() -> None:
    settings = _make_settings(
        ratelimit_content_max=1, ratelimit_content_window_s=60, trusted_proxies="10.0.0.0/8"
    )
    async with _make_client(settings, client_ip="10.0.0.1") as client:
        r1 = await client.get("/nldd/website/", headers={"x-forwarded-for": "10.0.0.2, 10.0.0.3"})
        r2 = await client.get("/nldd/website/", headers={"x-forwarded-for": "10.0.0.4"})
    # every entry trusted: both requests count on the peer (10.0.0.1) itself.
    assert [r1.status_code, r2.status_code] == [200, 429]


async def test_trusted_hop_right_becomes_skipped() -> None:
    settings = _make_settings(
        ratelimit_content_max=1,
        ratelimit_content_window_s=60,
        trusted_proxies="10.0.0.0/8",
    )
    async with _make_client(settings, client_ip="10.0.0.1") as client:
        # chain client -> proxy 10.0.0.9 -> peer: the trusted hop on the right
        # does not count, the real client (203.0.113.7) does.
        r1 = await client.get(
            "/nldd/website/", headers={"x-forwarded-for": "203.0.113.7, 10.0.0.9"}
        )
        r2 = await client.get(
            "/nldd/website/", headers={"x-forwarded-for": "203.0.113.7, 10.0.0.9"}
        )
    assert [r1.status_code, r2.status_code] == [200, 429]


async def test_exempt_path_not_limited() -> None:
    # `/healthz` is the only exemption. SPA assets are unlimited too, because
    # the SPA middleware sits outside this one (test_app_integration.py), not
    # because of a rule in here.
    settings = _make_settings(ratelimit_content_max=1, ratelimit_content_window_s=60)
    async with _make_client(settings) as client:
        for _ in range(20):
            assert (await client.get("/healthz")).status_code == 200


async def test_parallel_requests_count_exactly_no_escalation() -> None:
    """Concurrent requests must not count each other twice or too few times
    (no race in the counter), and there is no escalating penalty: exactly the
    first `max` requests succeed, the rest simply get a 429."""
    settings = _make_settings(
        ratelimit_api_max=10, ratelimit_api_window_s=60, ratelimit_api_global_max=1000
    )
    async with _make_client(settings) as client:
        results = await asyncio.gather(*[client.get("/-/api/v1/x") for _ in range(20)])

    statuses = [r.status_code for r in results]
    assert statuses.count(200) == 10
    assert statuses.count(429) == 10
    assert all(status in (200, 429) for status in statuses)  # no other status: no escalation


async def test_normal_click_behaviour_stays_under_content_limit() -> None:
    """A published site: 1 page + 30 asset requests within 10s, against the
    real default limit of the CONTENT class (600/60s)."""
    settings = _make_settings()
    clock = _FakeClock()
    async with _make_client(settings, clock=clock) as client:
        assert (await client.get("/nldd/website/")).status_code == 200

        clock.now_ = 10.0
        for i in range(30):
            resp = await client.get(f"/nldd/website/assets/bestand-{i}.js")
            assert resp.status_code == 200


async def test_make_ratelimit_middleware_factory_yields_class__and_kwargs() -> None:
    settings = _make_settings()
    klass, kwargs = make_rate_limit_middleware(settings)

    assert klass is RateLimitMiddleware
    assert kwargs["settings"] is settings
    assert isinstance(kwargs["counter"], InMemoryCounter)

    # the factory must be usable as middleware right away, e.g. via app.add_middleware(klasse, **kwargs)
    middleware = klass(app=_ok_app, **kwargs)
    transport = httpx.ASGITransport(app=middleware, client=("203.0.113.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
