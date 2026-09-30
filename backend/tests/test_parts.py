"""The split of the suite into CI parts (tests/parts.py): every test file runs in
exactly one part, and a mistyped part name fails instead of running nothing."""

from __future__ import annotations

from pathlib import Path

import conftest
import parts
import pytest

TESTS = Path(__file__).resolve().parent
TEST_FILES = {path.name for path in TESTS.glob("test_*.py")}


def test_every_named_file_exists() -> None:
    """A name that matches no file would leave that part lighter than it
    looks, and the file it meant would quietly run in `rest`."""
    for names in parts.NAMED.values():
        assert names <= TEST_FILES


def test_every_test_file_runs_in_exactly_one_part() -> None:
    for filename in TEST_FILES:
        assert [p for p in parts.PARTS if parts.runs_in(p, filename)] != [], filename
        assert len([p for p in parts.PARTS if parts.runs_in(p, filename)]) == 1, filename


def test_rest_takes_a_file_no_part_names() -> None:
    assert parts.runs_in(parts.REST, "test_something_new.py")
    assert not parts.runs_in("access", "test_something_new.py")


def test_without_a_part_everything_is_collected(monkeypatch) -> None:
    monkeypatch.delenv(parts.PART_VAR, raising=False)
    assert conftest.pytest_ignore_collect(TESTS / "test_serving.py", None) is None


def test_a_part_collects_only_its_own_files(monkeypatch) -> None:
    monkeypatch.setenv(parts.PART_VAR, "access")
    assert conftest.pytest_ignore_collect(TESTS / "test_serving.py", None) is False
    assert conftest.pytest_ignore_collect(TESTS / "test_oidc.py", None) is True
    # Helpers and conftest are never test files, so the hook leaves them alone.
    assert conftest.pytest_ignore_collect(TESTS / "helpers_oidc.py", None) is None


def test_an_unknown_part_is_refused(monkeypatch) -> None:
    monkeypatch.setenv(parts.PART_VAR, "acess")
    with pytest.raises(pytest.UsageError, match="acess"):
        conftest.pytest_ignore_collect(TESTS / "test_serving.py", None)
