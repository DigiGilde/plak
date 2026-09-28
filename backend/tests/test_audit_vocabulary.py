"""docs/audit-log.md and audit/vocabulary.py describe the same contract.

The audit table is append-only, so a name that drifts between the two is a
name that can never be taken back. This keeps them in step, and keeps the
code from writing a name that is in neither.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from plak.audit import vocabulary

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "audit-log.md").read_text(encoding="utf-8")
SRC = ROOT / "backend" / "src" / "plak"


def _backticked(text: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+|[A-Z_]+)`", text))


def test_every_action_in_the_code_is_documented() -> None:
    assert vocabulary.ACTIONS.issubset(_backticked(DOC))


def test_every_result_and_login_reason_is_documented() -> None:
    assert vocabulary.RESULTS.issubset(_backticked(DOC))
    assert vocabulary.LOGIN_REASONS.issubset(_backticked(DOC))


def test_every_ci_refusal_reason_is_documented() -> None:
    assert vocabulary.CI_REASONS.issubset(_backticked(DOC))


def test_the_admin_api_writes_no_action_outside_the_vocabulary() -> None:
    """The admin endpoints pass their action as a literal; one typo there would
    open a third vocabulary nobody knows about."""
    admin = (SRC / "api" / "admin.py").read_text(encoding="utf-8")
    written = set(re.findall(r'await _audit\(\s*request,\s*\w+,\s*"([a-z_]+)"', admin))
    written |= set(re.findall(r'MemberStatus\.\w+,\s*"([a-z_]+)"', admin))

    assert written
    assert written.issubset(vocabulary.ADMIN_ACTIONS)


def test_the_short_retention_is_about_looking_not_about_security() -> None:
    """Nothing refused lands in the short tier: every refusal can become part
    of an incident, which BIO2 5.28.01 keeps for three years."""
    for action, result in vocabulary.SHORT_RETENTION:
        assert action in vocabulary.ACTIONS
        assert result == vocabulary.ALLOWED


def test_the_long_retention_is_never_short_of_three_calendar_years() -> None:
    """BIO2 5.28.01 asks for three years. Whichever day a row is written, the
    term in days must reach the same date three years on, leap day or not."""
    start = date(2024, 1, 1)
    for offset in range(4 * 366):
        written = start + timedelta(days=offset)
        try:
            three_years_on = written.replace(year=written.year + 3)
        except ValueError:  # 29 February, three years on, is 1 March
            three_years_on = date(written.year + 3, 3, 1)
        assert (three_years_on - written).days <= vocabulary.LONG_RETENTION_DAYS
