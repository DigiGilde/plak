"""slug_window.py: through which moment an old address keeps redirecting.

The window ends at midnight in Amsterdam after the 30th day following the
rename, so the day of the rename and the moment the window closes are both
Amsterdam calendar days, whatever daylight saving time does in between.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plak.slug_window import AMSTERDAM, redirect_ends_at, still_redirects


def _amsterdam(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=AMSTERDAM)


def test_a_rename_redirects_through_the_thirtieth_day_after_it():
    retired_at = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

    end = redirect_ends_at(retired_at)

    assert end == _amsterdam(2026, 11, 7, 0, 0)
    assert end == datetime(2026, 11, 6, 23, 0, tzinfo=UTC)
    assert end.tzinfo is UTC


def test_one_second_before_the_end_it_still_redirects_and_at_the_end_no_more():
    retired_at = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    end = redirect_ends_at(retired_at)

    assert still_redirects(retired_at, end - timedelta(seconds=1))
    assert not still_redirects(retired_at, end)
    assert not still_redirects(retired_at, end + timedelta(days=1))


def test_a_rename_just_before_the_end_of_summer_time_ends_at_amsterdam_midnight_not_an_hour_off():
    """24 October 2026 is the last Saturday of October: summer time ends that
    night. Counting in UTC would put the end at 01:00 or 23:00 in Amsterdam."""
    retired_at = _amsterdam(2026, 10, 24, 23, 30)

    end = redirect_ends_at(retired_at)

    assert end == _amsterdam(2026, 11, 24, 0, 0)
    assert end == datetime(2026, 11, 23, 23, 0, tzinfo=UTC)


def test_a_rename_just_after_midnight_counts_that_day_as_day_zero():
    """00:10 in Amsterdam is still the day before in UTC; the day that counts
    is the Amsterdam one."""
    retired_at = _amsterdam(2026, 10, 8, 0, 10)
    assert retired_at.astimezone(UTC).day == 7

    assert redirect_ends_at(retired_at) == _amsterdam(2026, 11, 8, 0, 0)


def test_the_window_into_summer_time_ends_at_amsterdam_midnight_too():
    retired_at = _amsterdam(2026, 3, 1, 9, 0)

    end = redirect_ends_at(retired_at)

    assert end == _amsterdam(2026, 4, 1, 0, 0)
    assert end == datetime(2026, 3, 31, 22, 0, tzinfo=UTC)
