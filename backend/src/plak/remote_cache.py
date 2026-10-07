"""What Plak fetches from an identity provider or a CI issuer, cached per key.

The signing keys (JWKS) of the OIDC provider and of each CI issuer, and the
OIDC discovery document, all go through RemoteCache:

- a value is fresh for `ttl_s`, then fetched again, so a key the issuer
  withdraws stops being trusted within that time;
- one fetch per key at a time: callers that arrive meanwhile wait and share
  its result;
- after a failed fetch the key is not asked again for `failure_ttl_s`;
- a failed refetch with a stale value at hand serves that value and restarts
  its clock, so an outage at the issuer neither refuses every request nor
  turns each one into an outbound fetch;
- `force` fetches regardless of the TTL (a token names a kid the cached keys
  lack), unless another caller fetched while this one waited. How often a
  caller may force is its own call, through `claim_forced_refresh`.

The keys are configured issuers, so every dict here stays as small as that
list.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class RemoteUnavailableError(Exception):
    """No value to serve: the fetch failed (chained as the cause) or failed
    within the last `failure_ttl_s`."""


@dataclass
class _Entry[T]:
    value: T
    fetched_at: float


class RemoteCache[T]:
    def __init__(
        self,
        fetch: Callable[[str], Awaitable[T]],
        *,
        ttl_s: float,
        failure_ttl_s: float,
        errors: tuple[type[Exception], ...],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`errors` are the exceptions of `fetch` that count as a failed
        fetch; anything else propagates untouched."""
        self._fetch = fetch
        self._ttl_s = ttl_s
        self._failure_ttl_s = failure_ttl_s
        self._errors = errors
        self._clock = clock
        self._entries: dict[str, _Entry[T]] = {}
        self._failed_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._fetches: dict[str, int] = {}
        self._forced_at: dict[str, float] = {}

    async def get(self, key: str, *, force: bool = False) -> T:
        cached = self._entries.get(key)
        if cached is not None and not force and self._clock() - cached.fetched_at < self._ttl_s:
            return cached.value
        fetches_before = self._fetches.get(key, 0)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = self._clock()
            cached = self._entries.get(key)
            if cached is not None:
                fresh = now - cached.fetched_at < self._ttl_s
                # Someone else fetched while this request waited for the lock.
                if (not force and fresh) or (force and self._fetches.get(key, 0) > fetches_before):
                    return cached.value
            failed_at = self._failed_at.get(key)
            if failed_at is not None and now - failed_at < self._failure_ttl_s:
                if cached is not None and not force:
                    return cached.value
                raise RemoteUnavailableError(f"{key}: fetch failed less than {self._failure_ttl_s}s ago")
            try:
                value = await self._fetch(key)
            except self._errors as error:
                self._failed_at[key] = now
                if cached is not None and not force:
                    cached.fetched_at = now
                    return cached.value
                raise RemoteUnavailableError(f"{key}: {error}") from error
            self._failed_at.pop(key, None)
            self._fetches[key] = self._fetches.get(key, 0) + 1
            self._entries[key] = _Entry(value=value, fetched_at=now)
            return value

    def claim_forced_refresh(self, key: str, cooldown_s: float) -> bool:
        """Whether a forced fetch for `key` may go out now: at most once per
        `cooldown_s`, so a stream of tokens naming unknown kids does not turn
        into a fetch storm. Claims the slot when it answers yes."""
        now = self._clock()
        forced_at = self._forced_at.get(key)
        if forced_at is not None and now - forced_at < cooldown_s:
            return False
        self._forced_at[key] = now
        return True


__all__ = ["RemoteCache", "RemoteUnavailableError"]
