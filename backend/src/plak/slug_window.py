"""How long a retired slug keeps redirecting: through the 30th day after the
rename, until midnight in Amsterdam. The database records only when a slug
was retired; everything about the window is decided here."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from plak.constants import SLUG_REDIRECT_DAYS

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def redirect_ends_at(retired_at: datetime) -> datetime:
    """The Amsterdam midnight that starts the 31st day after the rename, in
    UTC. Counted in calendar days of Amsterdam, so the day of the rename is
    day 0 there and a change of daylight saving time in between moves
    nothing."""
    local_day = retired_at.astimezone(AMSTERDAM).date()
    end = datetime.combine(local_day + timedelta(days=SLUG_REDIRECT_DAYS + 1), time.min, tzinfo=AMSTERDAM)
    return end.astimezone(UTC)


def still_redirects(retired_at: datetime, now: datetime) -> bool:
    return now < redirect_ends_at(retired_at)
