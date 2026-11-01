"""The fixtures the tests of scripts/release.py share: a repository on
beta with the files a release touches, in the states the subcommands
meet."""

from __future__ import annotations

import os
from datetime import date

import pytest
from release_support import (
    BACKEND_LOCK,
    BACKEND_PYPROJECT,
    FRAGMENT,
    LOCK,
    MAIN,
    PACKAGE,
    PACKAGE_LOCK,
    PLUGIN,
    PUBLICCODE,
    PYPROJECT,
    Repo,
    changelog_text,
)


@pytest.fixture(autouse=True)
def _git_env(monkeypatch) -> None:
    """No global or system git config: a signing or hook setting on the
    machine must not decide whether these tests pass."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.nl")
    for variable in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Repo:
    """A repository on `beta` with the files a release touches, no tags yet."""
    repo = Repo(tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "beta")
    repo.commit(
        {
            "CHANGELOG.md": changelog_text(),
            "cli/pyproject.toml": PYPROJECT,
            "cli/uv.lock": LOCK,
            "cli/plak_cli/__init__.py": "x = 1\n",
            "plugin/.claude-plugin/plugin.json": PLUGIN,
            "plugin/skills/plak/SKILL.md": "skill\n",
            "publiccode.yml": PUBLICCODE,
            "backend/src/toon/main.py": MAIN,
            "backend/pyproject.toml": BACKEND_PYPROJECT,
            "backend/uv.lock": BACKEND_LOCK,
            "frontend/package.json": PACKAGE,
            "frontend/package-lock.json": PACKAGE_LOCK,
            "README.md": "readme\n",
        },
        "Start",
    )
    monkeypatch.chdir(repo.path)
    return repo


@pytest.fixture
def released(repo) -> Repo:
    """The same repository after its first release, v2026.9.1."""
    repo.commit(FRAGMENT)
    repo.release("v2026.9.1", date(2026, 9, 1))
    return repo


@pytest.fixture
def branch(released) -> Repo:
    """A feature branch off beta, which has had one release."""
    released.git("switch", "-q", "-c", "feature")
    return released


@pytest.fixture
def pushed(branch) -> Repo:
    """The feature branch with a shipped change and no fragment, and an
    origin/beta to compare with."""
    branch.git("update-ref", "refs/remotes/origin/beta", "beta")
    branch.commit({"backend/src/toon/main.py": "app = 2\n"})
    return branch
