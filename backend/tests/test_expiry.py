"""plak/expiry.py: every issued access right gets an end date (BIO2 5.18.02)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from plak.expiry import CLOCK_SKEW, DEFAULT_VALIDITY, MAX_VALIDITY, ExpiryError, resolve


def test_no_date_takes_the_default() -> None:
    before = datetime.now(UTC)
    resolved = resolve(None)
    assert before + DEFAULT_VALIDITY <= resolved <= datetime.now(UTC) + DEFAULT_VALIDITY


def test_the_longest_choice_survives_a_visitor_clock_that_runs_ahead() -> None:
    """The interface computes "365 dagen" on the browser's clock; a few
    seconds ahead of the server must not turn its own longest option into a
    refusal."""
    ahead = datetime.now(UTC) + timedelta(seconds=30)
    assert resolve(ahead + MAX_VALIDITY) == ahead + MAX_VALIDITY


def test_beyond_the_maximum_and_the_skew_is_refused() -> None:
    with pytest.raises(ExpiryError) as refused:
        resolve(datetime.now(UTC) + MAX_VALIDITY + CLOCK_SKEW + timedelta(minutes=1))
    assert refused.value.reason == "EXPIRY_TOO_FAR"


def test_the_past_is_refused() -> None:
    with pytest.raises(ExpiryError) as refused:
        resolve(datetime.now(UTC) - timedelta(seconds=1))
    assert refused.value.reason == "EXPIRY_IN_PAST"
