"""Validity of issued access rights: deploy tokens and secret links.

BIO2 5.18.02 asks that every issued access right is reviewed at least once a
year. A right without an end date is never reviewed, so `expires_at` is never
empty here: leaving it out takes the default, and a date beyond the maximum is
refused rather than silently shortened.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from plak import i18n, messages
from plak.messages import Msg

DEFAULT_VALIDITY = timedelta(days=90)
MAX_VALIDITY = timedelta(days=365)
# The interface turns "365 dagen" into a timestamp on the visitor's clock. A
# clock that runs a few seconds ahead of the server would otherwise have the
# longest choice the interface offers refused.
CLOCK_SKEW = timedelta(hours=1)

REASON_EXPIRY_IN_PAST = "EXPIRY_IN_PAST"
REASON_EXPIRY_TOO_FAR = "EXPIRY_TOO_FAR"


class ExpiryError(ValueError):
    """Refused expiry date, with a machine-readable reason code and a message
    key from plak/messages.py."""

    def __init__(self, key: str, *, params: Mapping[str, object] | None = None) -> None:
        self.message = Msg(key, dict(params or {}))
        self.reason = messages.code_of(key)
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


def resolve(expires_at: datetime | None) -> datetime:
    """The end date to store; the default when the caller named none."""
    now_ = datetime.now(UTC)
    if expires_at is None:
        return now_ + DEFAULT_VALIDITY
    if expires_at <= now_:
        raise ExpiryError(REASON_EXPIRY_IN_PAST)
    if expires_at > now_ + MAX_VALIDITY + CLOCK_SKEW:
        raise ExpiryError(REASON_EXPIRY_TOO_FAR, params={"days": MAX_VALIDITY.days})
    return expires_at


__all__ = [
    "CLOCK_SKEW",
    "DEFAULT_VALIDITY",
    "MAX_VALIDITY",
    "REASON_EXPIRY_IN_PAST",
    "REASON_EXPIRY_TOO_FAR",
    "ExpiryError",
    "resolve",
]
