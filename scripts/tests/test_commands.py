"""The subcommands of scripts/release.py (toon_release.commands): decide,
prepare, verify-staged, notes, check, api-check and the Claude Code hook.

The git side runs against real repositories in tmp_path, so what is pinned
down here is what git does, not what a mock says it does."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from datetime import date

import pytest
from release_support import (
    BACKEND_LOCK,
    BACKEND_PYPROJECT,
    DAY,
    ENTRY,
    FRAGMENT,
    LOCK,
    MAIN,
    NO_CHANGES,
    NOTE_FRAGMENTS,
    NOTES,
    PACKAGE,
    PUBLICCODE,
    PYPROJECT,
    RELEASED_SECTION,
    ROOT,
    SPEC,
    TAG,
    Repo,
    _event,
    _image_versions,
    _versions,
    change,
    changelog_text,
    fake_oasdiff,
    release,
    spec_with,
)

from toon_release import changelog, commands, errors, versions


class TestReleaseProblems:
    """What a commit has to look like to be tagged as a release."""

    @pytest.fixture
    def pending(self, released) -> Repo:
        """A release prepared on top of v2026.9.1 and committed, not tagged."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        return released

    def test_a_prepared_release_passes(self, pending):
        assert commands.release_problems(pending.handle, "HEAD", "2026.10.1") == []

    @pytest.mark.parametrize(
        ("files", "path"),
        [
            ({"backend/pyproject.toml": '[project]\nname = "toon-api"\n'}, "backend/pyproject.toml"),
            ({"backend/pyproject.toml": BACKEND_PYPROJECT}, "backend/pyproject.toml"),
            ({"frontend/package.json": "{"}, "frontend/package.json"),
            ({"frontend/package.json": "[]\n"}, "frontend/package.json"),
            ({"frontend/package.json": PACKAGE}, "frontend/package.json"),
            ({"publiccode.yml": "name: Plak\n"}, "publiccode.yml"),
            ({"publiccode.yml": "softwareVersion: 2026.9.1\n"}, "publiccode.yml"),
        ],
        ids=[
            "pyproject-without-version",
            "pyproject-old-version",
            "package-not-json",
            "package-not-an-object",
            "package-old-version",
            "publiccode-without-line",
            "publiccode-old-version",
        ],
    )
    def test_a_version_file_that_does_not_carry_the_version_fails(self, pending, files, path):
        pending.commit(files)
        assert commands.release_problems(pending.handle, "HEAD", "2026.10.1") == [
            f"{path} at HEAD does not carry version 2026.10.1."
        ]

    def test_a_version_file_that_is_missing_fails(self, pending):
        pending.remove("publiccode.yml")
        assert commands.release_problems(pending.handle, "HEAD", "2026.10.1") == [
            "publiccode.yml at HEAD does not carry version 2026.10.1."
        ]

    def test_a_fragment_left_behind_fails(self, pending):
        pending.commit({"changelog.d/late.added.md": "- Late.\n", "changelog.d/README.md": "Fine here.\n"})
        assert commands.release_problems(pending.handle, "HEAD", "2026.10.1") == [
            "changelog.d/late.added.md is still there: a release removes every fragment."
        ]

    def test_a_version_behind_the_newest_tag_fails(self, released):
        problems = commands.release_problems(released.handle, "HEAD", "2026.8.1")
        assert problems[0] == "v2026.8.1 is not newer than v2026.9.1."

    def test_a_version_that_is_no_calver_fails(self, released):
        problems = commands.release_problems(released.handle, "HEAD", "Unreleased")
        assert problems[0].startswith("'vUnreleased' is not a CalVer tag")


class TestDecide:
    def test_without_a_section_there_is_nothing_to_tag(self, repo):
        assert commands.decide(repo.handle) == ("none", "", "")

    def test_a_tagged_newest_section_releases_nothing(self, released):
        """Also what a rerun of the workflow sees after the tag went out."""
        assert commands.decide(released.handle) == ("none", "", "")

    def test_a_change_without_a_release_releases_nothing(self, released):
        released.commit({"backend/src/toon/main.py": "app = 2\n", **FRAGMENT})
        assert commands.decide(released.handle) == ("none", "", "")

    def test_an_untagged_newest_section_is_tagged_at_the_commit_that_added_it(self, released):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        released.commit({"README.md": "later\n"})
        assert commands.decide(released.handle) == ("release", TAG, commit)

    def test_an_edit_in_the_pull_request_moves_the_tag_to_the_edit(self, released):
        """beta merges by rebase, so a reword made in the pull request lands
        as a commit of its own after the release commit; the tag has to
        carry it."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        released.commit({"CHANGELOG.md": released.read("CHANGELOG.md").replace("Something new", "Something newer")})
        released.commit({NOTES + "2026.10.1.nl.md": "Nieuw.\n", NOTES + "2026.10.1.en.md": "New.\n"})
        edit = released.head()
        released.commit({"README.md": "later\n"})
        assert commands.decide(released.handle) == ("release", TAG, edit)

    def test_a_commit_that_adds_or_edits_a_fragment_ends_the_release(self, released):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        reworded = released.read("CHANGELOG.md") + "\n"
        released.commit({"changelog.d/late.added.md": "- Late.\n", "CHANGELOG.md": reworded})
        assert commands.release_head(released.handle, "2026.10.1") == commit
        released.commit({"changelog.d/late.added.md": "- Later.\n"})
        assert commands.release_head(released.handle, "2026.10.1") == commit

    def test_a_commit_that_removes_a_fragment_is_part_of_the_release(self, released):
        """A fragment that reached beta after the release was prepared, with
        the release rebased on top of it: taken out in the pull request, its
        line added to the section, and the tag carries that commit."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        release_commit = released.head()
        released.git("switch", "-q", "--detach", "HEAD^")
        released.commit({"changelog.d/late.added.md": "- Late.\n"})
        released.git("cherry-pick", release_commit)
        released.git("rm", "-q", "changelog.d/late.added.md")
        with_late = released.read("CHANGELOG.md").replace("- Something new.", "- Something new.\n- Late.")
        released.commit({"CHANGELOG.md": with_late})
        released.git("switch", "-q", "-C", "beta")
        assert commands.decide(released.handle) == ("release", TAG, released.head())

    def test_a_fragment_added_after_the_release_is_the_next_releases(self, released):
        """It ends the release, which is tagged without it."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        released.commit({"changelog.d/late.added.md": "- Late.\n"})
        assert commands.decide(released.handle) == ("release", TAG, commit)

    def test_a_release_commit_that_is_not_releasable_fails(self, released):
        """The job goes red instead of tagging a commit that left a fragment
        behind or forgot a version."""
        released.commit({"CHANGELOG.md": changelog_text(f"## [2026.10.1]\n\n- Hand made.\n\n## [2026.9.1]\n\n{ENTRY}")})
        with pytest.raises(errors.ReleaseError) as refused:
            commands.decide(released.handle)
        assert refused.value.problems[0].startswith("backend/pyproject.toml at ")
        assert refused.value.problems[0].endswith(" does not carry version 2026.10.1.")

    def test_a_malformed_newest_heading_fails(self, released):
        released.commit({"CHANGELOG.md": changelog_text(f"## [Next]\n\n- x\n\n## [2026.9.1]\n\n{ENTRY}")})
        with pytest.raises(errors.ReleaseError, match="'vNext' is not a CalVer tag"):
            commands.decide(released.handle)

    def test_main_prints_the_route_and_writes_the_step_output(self, released, monkeypatch, tmp_path, capsys):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        output = tmp_path / "output"
        output.write_text("earlier=1\n")
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))

        assert release.main(["decide"]) == 0
        assert capsys.readouterr().out == f"route=release\ntag={TAG}\ncommit={commit}\n"
        assert output.read_text() == f"earlier=1\nroute=release\ntag={TAG}\ncommit={commit}\n"

    def test_main_without_a_step_output_only_prints(self, repo, capsys):
        assert release.main(["decide"]) == 0
        assert capsys.readouterr().out == "route=none\ntag=\ncommit=\n"

    def test_main_outside_a_repository_fails(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert release.main(["decide"]) == 1
        assert capsys.readouterr().err.startswith("error: git rev-parse --show-toplevel:")


class TestPrepare:
    def test_the_fragments_become_the_section_for_the_tag(self, repo):
        older = "## [2026.9.1]\n\n### Fixed\n\n- Old."
        repo.commit({"CHANGELOG.md": changelog_text(older), "changelog.d/README.md": "Read docs/releasing.md.\n"})
        repo.git("tag", "v2026.9.1")
        repo.commit({**FRAGMENT, "changelog.d/a-fix.fixed.md": "- Fixed it,\n  at last.\n"})

        tag, touched = commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

        assert tag == TAG
        assert repo.read("CHANGELOG.md") == (
            "# Changelog\n\nWhat changed, newest first.\n\n"
            f"## [Unreleased]\n\n{changelog.UNRELEASED_POINTER}\n\n"
            f"## [2026.10.1]\n\n{ENTRY}\n\n### Fixed\n\n- Fixed it,\n  at last.\n\n{older}\n\n"
            "[Unreleased]: https://github.com/DigiGilde/plak/compare/v2026.10.1...HEAD\n"
            "[2026.10.1]: https://github.com/DigiGilde/plak/compare/v2026.9.1...v2026.10.1\n"
            "[2026.9.1]: https://github.com/DigiGilde/plak/releases/tag/v2026.9.1\n"
        )
        assert changelog.changelog_problems(repo.read("CHANGELOG.md")) == []
        staged = repo.git("diff", "--cached", "--name-only").split()
        assert sorted(staged) == sorted(touched)
        assert sorted(os.listdir(repo.path / "changelog.d")) == ["README.md"]
        assert "D  changelog.d/something-new.added.md" in repo.git("status", "--porcelain").splitlines()

    def test_the_tag_is_todays_unless_given(self, released):
        released.commit(FRAGMENT)
        tag, _ = commands.prepare(released.handle, DAY, SPEC, NO_CHANGES, "v2026.10.5")
        assert tag == "v2026.10.5"
        assert "## [2026.10.5]" in released.read("CHANGELOG.md")
        assert 'softwareVersion: "2026.10.5"\nreleaseDate: "2026-10-05"' in released.read("publiccode.yml")

    def test_the_first_release_sets_every_component_version(self, repo):
        repo.commit(FRAGMENT)
        commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

        assert _versions(repo) == ("2026.10.1", "2026.10.1")
        assert 'name = "plak"\nversion = "2026.10.1"' in repo.read("cli/uv.lock")
        assert 'softwareVersion: "2026.10.1"\nreleaseDate: "2026-10-01"' in repo.read("publiccode.yml")
        assert _image_versions(repo) == {"2026.10.1"}

    def test_a_backend_change_leaves_the_cli_and_plugin_versions(self, released):
        released.commit({**FRAGMENT, "backend/src/toon/app.py": "app = 2\n"})
        _, touched = commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)

        assert _versions(released) == ("2026.9.1", "2026.9.1")
        assert sorted(touched) == [
            "CHANGELOG.md",
            "backend/openapi.json",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "changelog.d/something-new.added.md",
            "frontend/package-lock.json",
            "frontend/package.json",
            "publiccode.yml",
        ]
        assert 'softwareVersion: "2026.10.1"' in released.read("publiccode.yml")

    def test_the_image_gets_every_release_also_when_only_the_cli_changed(self, released):
        """Every tag builds and ships a new image, whatever changed."""
        released.commit({**FRAGMENT, "cli/plak_cli/__init__.py": "x = 2\n"})
        commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert _image_versions(released) == {"2026.10.1"}
        assert 'name = "plak"\nversion = "9.9.9"' in released.read("backend/uv.lock")

    def test_a_missing_image_package_file_stops_the_release(self, released):
        released.remove("frontend/package-lock.json")
        released.commit(FRAGMENT)
        with pytest.raises(errors.ReleaseError, match=re.escape("frontend/package-lock.json is missing")):
            commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert released.git("status", "--porcelain") == ""

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({"cli/plak_cli/__init__.py": "x = 2\n"}, ("2026.10.1", "2026.9.1")),
            ({"cli/pyproject.toml": PYPROJECT.replace('["httpx"]', '["httpx", "keyring"]')}, ("2026.10.1", "2026.9.1")),
            ({"cli/uv.lock": LOCK.replace("0.28.1", "0.28.2")}, ("2026.10.1", "2026.9.1")),
            ({"plugin/skills/plak/SKILL.md": "better\n"}, ("2026.9.1", "2026.10.1")),
            ({"cli/tests/test_x.py": "def test(): pass\n"}, ("2026.9.1", "2026.9.1")),
            ({"plugin/evals/x/prompt.md": "zet dit online\n"}, ("2026.9.1", "2026.9.1")),
        ],
        ids=["cli-code", "cli-dependency", "cli-lock", "plugin-skill", "cli-test-only", "plugin-evals-only"],
    )
    def test_a_component_gets_a_new_version_only_when_it_changed(self, released, files, expected):
        released.commit({**FRAGMENT, **files})
        commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert _versions(released) == expected

    def test_a_version_line_changed_by_hand_is_no_change(self, released):
        """Only the line a release writes itself is left out of the
        comparison; everything around it still counts."""
        released.commit(
            {
                **FRAGMENT,
                "cli/pyproject.toml": versions.set_pyproject_version(released.read("cli/pyproject.toml"), "9.9.9"),
                "cli/uv.lock": versions.set_lock_version(released.read("cli/uv.lock"), "9.9.9"),
                "plugin/.claude-plugin/plugin.json": versions.set_plugin_version(
                    released.read("plugin/.claude-plugin/plugin.json"), "9.9.9"
                ),
            }
        )
        commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert _versions(released) == ("9.9.9", "9.9.9")

    def test_a_file_that_did_not_exist_at_the_last_tag_is_a_change(self, repo):
        section = "## [2026.9.1]\n\n- x"
        repo.remove("cli/uv.lock")
        repo.commit({"CHANGELOG.md": changelog_text(section)})
        repo.git("tag", "v2026.9.1")
        repo.commit({**FRAGMENT, "cli/uv.lock": LOCK})
        commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert _versions(repo) == ("2026.10.1", "0.3.1")

    def test_the_fragments_are_read_from_the_commit_not_the_working_tree(self, released):
        """A fragment that is not committed is not released, and an ignored
        file next to the fragments plays no part."""
        released.commit({".gitignore": ".DS_Store\nuncommitted.added.md\n"})
        released.write({"changelog.d/uncommitted.added.md": "- Not yet.\n", "changelog.d/.DS_Store": "junk"})
        with pytest.raises(errors.ReleaseError, match="Nothing to release"):
            commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        released.commit(FRAGMENT)
        _, touched = commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert "changelog.d/something-new.added.md" in touched
        assert "Not yet." not in released.read("CHANGELOG.md")
        assert (released.path / "changelog.d" / ".DS_Store").exists()

    @pytest.mark.parametrize("mode", ["symlink", "executable"])
    def test_a_fragment_that_is_not_a_plain_file_is_refused(self, released, mode):
        released.commit(FRAGMENT)
        if mode == "symlink":
            os.symlink("../README.md", released.path / "changelog.d" / "link.fixed.md")
            released.commit()
            name = "link.fixed.md"
        else:
            released.git("update-index", "--chmod=+x", "changelog.d/something-new.added.md")
            released.git("commit", "-q", "-m", "Mode")
            name = "something-new.added.md"
        with pytest.raises(errors.ReleaseError) as refused:
            commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert refused.value.problems == [f"changelog.d/{name} is not a plain file."]

    def test_a_shipped_change_without_an_entry_is_refused_and_writes_nothing(self, released):
        """Without entries there are no release notes, so nothing is made up
        from commit subjects either."""
        released.commit({"backend/src/toon/main.py": "app = 2\n"}, "Update urllib3 to 2.8.0")
        with pytest.raises(errors.ReleaseError) as refused:
            commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert refused.value.problems == [
            "Nothing to release: changelog.d/ has no entries, and they are the release notes."
        ]
        assert released.git("status", "--porcelain") == ""

    def test_without_a_tag_or_entries_there_is_nothing_to_release(self, repo):
        with pytest.raises(errors.ReleaseError, match="Nothing to release"):
            commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

    @pytest.mark.parametrize(
        ("files", "tag", "reason"),
        [
            (NOTE_FRAGMENTS, None, "Nothing to release"),
            ({"changelog.d/bad name.md": "- x\n"}, None, "changelog.d/bad name.md is not <slug>"),
            ({"changelog.d/sub/x.added.md": "- x\n"}, None, "changelog.d/sub/x.added.md is not <slug>"),
            ({"changelog.d/x.added.md": "Prose.\n"}, None, "'Prose.' is not an entry"),
            ({**FRAGMENT, "changelog.d/x.note.nl.md": "a\n"}, None, "x.note.nl.md has no x.note.en.md"),
            (FRAGMENT, "v2026.10.01", "leading zero"),
            (FRAGMENT, "v2026.9.1", "already exists"),
            (FRAGMENT, "v2026.8.1", "not newer than v2026.9.1"),
        ],
        ids=[
            "notes-only",
            "bad-name",
            "subdirectory",
            "bad-content",
            "unpaired-note",
            "invalid-tag",
            "tag-exists",
            "older-tag",
        ],
    )
    def test_refusals_write_nothing(self, released, files, tag, reason):
        released.commit(files)
        with pytest.raises(errors.ReleaseError, match=re.escape(reason)):
            commands.prepare(released.handle, DAY, SPEC, NO_CHANGES, tag)
        assert released.git("status", "--porcelain") == ""

    def test_a_release_waiting_for_its_tag_blocks_the_next(self, released):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        released.commit({"changelog.d/next.added.md": "- Next.\n"})
        with pytest.raises(errors.ReleaseError) as refused:
            commands.prepare(released.handle, date(2026, 10, 2), SPEC, NO_CHANGES)
        assert refused.value.problems == ["v2026.10.1 is waiting for its tag: merge or close that release first."]
        assert released.git("status", "--porcelain") == ""

    def test_a_section_for_the_tag_already_in_the_changelog_is_refused(self, repo):
        """Even one further down, which no tag answers to yet."""
        repo.commit({"CHANGELOG.md": changelog_text("## [2026.9.1]\n\n- y\n\n## [2026.10.1]\n\n- x"), **FRAGMENT})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(errors.ReleaseError, match=f"already has a section for {TAG}"):
            commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES, TAG)

    def test_a_refusal_writes_nothing(self, repo):
        repo.commit(FRAGMENT)
        repo.remove("publiccode.yml")
        with pytest.raises(errors.ReleaseError, match=re.escape("publiccode.yml is missing")):
            commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert repo.git("status", "--porcelain") == ""

    def test_the_note_fragments_become_the_notes_of_the_version(self, repo):
        repo.commit(
            {
                **FRAGMENT,
                "changelog.d/b.note.nl.md": "## B\n\nTekst.\n",
                "changelog.d/a.note.nl.md": "## A\n\nTekst A.",
                "changelog.d/a.note.en.md": "## A\n\nText A.\n\n",
                "changelog.d/b.note.en.md": "## B\n\nText.\n",
            }
        )
        _, touched = commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

        assert sorted(os.listdir(repo.path / NOTES)) == ["2026.10.1.en.md", "2026.10.1.nl.md"]
        assert repo.read(NOTES + "2026.10.1.nl.md") == "## A\n\nTekst A.\n\n## B\n\nTekst.\n"
        assert repo.read(NOTES + "2026.10.1.en.md") == "## A\n\nText A.\n\n## B\n\nText.\n"
        status = repo.git("status", "--porcelain").splitlines()
        assert f"A  {NOTES}2026.10.1.nl.md" in status
        assert "D  changelog.d/a.note.nl.md" in status
        assert {NOTES + "2026.10.1.nl.md", NOTES + "2026.10.1.en.md", "changelog.d/b.note.en.md"} <= set(touched)
        assert not (repo.path / "changelog.d").exists()

    def test_without_note_fragments_there_are_no_notes(self, repo):
        repo.commit(FRAGMENT)
        _, touched = commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert not (repo.path / NOTES).exists()
        assert not any(path.startswith(NOTES) for path in touched)

    def test_a_note_for_the_version_that_exists_already_is_refused(self, repo):
        repo.commit({**FRAGMENT, **NOTE_FRAGMENTS, NOTES + "2026.10.1.nl.md": "a\n", NOTES + "2026.10.1.en.md": "b\n"})
        with pytest.raises(errors.ReleaseError, match=re.escape(f"{NOTES}2026.10.1.en.md already exists.")):
            commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert repo.git("status", "--porcelain") == ""

    def test_main_prints_the_tag_first_and_then_what_it_touched(self, repo, monkeypatch, tmp_path, capsys):
        repo.commit(FRAGMENT)
        # main() passes today() to prepare, so the entry's name is the one to patch.
        monkeypatch.setattr(release, "today", lambda: DAY)
        output = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["prepare", "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "unused"]
        assert release.main(args) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0] == f"tag={TAG}"
        assert "updated CHANGELOG.md" in out
        assert "updated backend/openapi.json" in out
        assert "updated changelog.d/something-new.added.md" in out
        assert output.read_text() == f"tag={TAG}\n"

    def test_main_takes_the_tag(self, repo, tmp_path, capsys):
        repo.commit(FRAGMENT)
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["prepare", "--tag", "v2026.10.5", "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "unused"]
        assert release.main(args) == 0
        assert capsys.readouterr().out.startswith("tag=v2026.10.5\n")

    def test_main_reports_a_refusal(self, repo, capsys):
        assert release.main(["prepare", "--spec", "x", "--oasdiff", "x"]) == 1
        assert "error: x: cannot read the API schema." in capsys.readouterr().err

    def test_main_reports_a_refused_tag(self, repo, tmp_path, capsys):
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["prepare", "--tag", "v2026.1", "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "x"]
        assert release.main(args) == 1
        assert "error: 'v2026.1' is not a CalVer tag" in capsys.readouterr().err


class TestReleaseNotes:
    def test_the_body_is_the_section_with_the_component_versions(self, released):
        released.commit({"changelog.d/a-fix.fixed.md": "- A fix.\n", "cli/plak_cli/__init__.py": "x = 2\n"})
        released.release(TAG, DAY)

        assert commands.release_notes(released.handle, TAG) == (
            "### Fixed\n\n- A fix.\n\nCLI 2026.10.1\n\nPlugin unchanged (2026.9.1)\n\nAPI 1.0.0\n"
        )

    def test_it_reads_the_tag_not_the_working_tree(self, released):
        released.commit({"CHANGELOG.md": changelog_text("## [2026.9.1]\n\n- Reworded.")})
        assert commands.release_notes(released.handle, "v2026.9.1").startswith(ENTRY + "\n\nCLI 2026.9.1")

    @pytest.mark.parametrize("tag", ["v2026.9.2", "v2026.1"], ids=["no-such-tag", "invalid"])
    def test_a_tag_without_a_section_is_refused(self, released, tag):
        with pytest.raises(errors.ReleaseError):
            commands.release_notes(released.handle, tag)

    def test_an_empty_section_is_refused(self, repo):
        repo.commit({"CHANGELOG.md": changelog_text("## [2026.9.1]\n")})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(errors.ReleaseError, match=re.escape("No section for v2026.9.1")):
            commands.release_notes(repo.handle, "v2026.9.1")

    def test_a_tag_without_the_manifests_is_refused(self, repo):
        repo.remove("plugin/.claude-plugin/plugin.json")
        repo.commit({"CHANGELOG.md": changelog_text("## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(errors.ReleaseError, match=re.escape("is missing at v2026.9.1")):
            commands.release_notes(repo.handle, "v2026.9.1")

    def test_main_prints_the_notes(self, released, capsys):
        assert release.main(["notes", "--tag", "v2026.9.1"]) == 0
        assert capsys.readouterr().out.endswith("CLI 2026.9.1\n\nPlugin 2026.9.1\n\nAPI 1.0.0\n")

    def test_with_the_image_it_ends_with_how_to_verify_it(self, released):
        digest = "sha256:" + "a" * 64
        notes = commands.release_notes(released.handle, "v2026.9.1", "ghcr.io/digigilde/plak", digest)
        assert notes.endswith(
            "API 1.0.0\n\n## Container image\n\n"
            f"- plak: `ghcr.io/digigilde/plak:2026.9.1`, digest `{digest}`\n\n"
            f"Verify where it was built: `gh attestation verify oci://ghcr.io/digigilde/plak@{digest} "
            "-R DigiGilde/plak`\n"
            "Its SBOM (CycloneDX): the same command with `--predicate-type https://cyclonedx.org/bom`\n"
        )

    @pytest.mark.parametrize(
        ("image", "digest"),
        [
            ("ghcr.io/digigilde/plak", None),
            (None, "sha256:" + "a" * 64),
            ("ghcr.io/DigiGilde/plak", "sha256:" + "a" * 64),
            ("ghcr.io/digigilde/plak`; rm", "sha256:" + "a" * 64),
            ("ghcr.io/digigilde/plak", "sha256:abc"),
        ],
        ids=["no-digest", "no-image", "upper-case", "markdown-break", "short-digest"],
    )
    def test_an_image_or_digest_that_is_not_one_is_refused(self, released, image, digest):
        """Both come from the build job; whatever else lands in the notes
        is refused rather than published."""
        with pytest.raises(errors.ReleaseError, match="not an image name and a sha256 digest"):
            commands.release_notes(released.handle, "v2026.9.1", image, digest)

    def test_main_prints_the_container_section(self, released, capsys):
        digest = "sha256:" + "b" * 64
        args = ["notes", "--tag", "v2026.9.1", "--image", "ghcr.io/digigilde/plak", "--digest", digest]
        assert release.main(args) == 0
        assert f"oci://ghcr.io/digigilde/plak@{digest}" in capsys.readouterr().out

    def test_a_tag_from_before_the_schema_has_no_api_line(self, repo):
        """v2026.9.30 was tagged by hand, before releases wrote the schema."""
        repo.commit({"CHANGELOG.md": changelog_text("## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        assert commands.release_notes(repo.handle, "v2026.9.1").endswith("Plugin unchanged (0.3.1)\n")


class TestPrepareTheApi:
    def test_the_first_release_commits_the_schema_at_the_version_the_code_has(self, repo):
        def unused(base, head):
            raise AssertionError("nothing to compare with")

        repo.commit(FRAGMENT)
        commands.prepare(repo.handle, DAY, SPEC, unused)
        assert json.loads(repo.read("backend/openapi.json"))["info"]["version"] == "1.0.0"
        assert repo.read("backend/src/toon/main.py") == MAIN

    def test_a_hand_made_tag_without_a_schema_counts_as_the_first(self, repo):
        repo.commit({"CHANGELOG.md": changelog_text("## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        repo.commit(FRAGMENT)
        commands.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert json.loads(repo.read("backend/openapi.json"))["info"]["version"] == "1.0.0"

    def test_an_addition_raises_the_minor_in_the_schema_and_the_code(self, released):
        head = spec_with(paths=("/a", "/b"))
        seen = []

        def diff(base, revision):
            seen.append((base, revision))
            return [change(1)]

        released.commit(FRAGMENT)
        commands.prepare(released.handle, DAY, head, diff)

        assert seen == [(released.git("show", "v2026.9.1:backend/openapi.json"), head)]
        assert json.loads(released.read("backend/openapi.json")) == {
            **json.loads(head),
            "info": {**json.loads(head)["info"], "version": "1.1.0"},
        }
        assert 'API_VERSION = "1.1.0"' in released.read("backend/src/toon/main.py")

    def test_a_breaking_change_stops_the_release_before_it_writes(self, released):
        released.commit(FRAGMENT)
        with pytest.raises(errors.ReleaseError, match="Breaking API change"):
            commands.prepare(released.handle, DAY, spec_with(paths=()), lambda b, h: [change(3)])
        assert released.git("status", "--porcelain") == ""

    def test_a_schema_that_is_not_json_stops_the_release(self, repo):
        repo.commit(FRAGMENT)
        with pytest.raises(errors.ReleaseError, match="The API schema is not JSON"):
            commands.prepare(repo.handle, DAY, "{", NO_CHANGES)

    def test_a_committed_schema_that_is_not_json_stops_the_release(self, released):
        released.commit(FRAGMENT)
        released.git("tag", "-d", "v2026.9.1")
        released.git("checkout", "-q", "HEAD~1")
        released.commit({"backend/openapi.json": "{"})
        released.git("tag", "v2026.9.1")
        released.git("checkout", "-q", "-")
        with pytest.raises(errors.ReleaseError, match=r"backend/openapi\.json at v2026\.9\.1 is not JSON"):
            commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)


class TestVerifyStaged:
    """The job that commits applies a patch from a job that ran the
    dependencies, then pushes it with the App. So what it commits has to
    be exactly what prepare writes."""

    @pytest.fixture(autouse=True)
    def _today(self, monkeypatch) -> None:
        monkeypatch.setattr(commands, "today", lambda: DAY)

    @pytest.fixture
    def prepared(self, released) -> Repo:
        """v2026.9.1 released, then a change with an entry and a note,
        prepared as TAG with an addition to the API."""
        released.commit({**FRAGMENT, **NOTE_FRAGMENTS, "cli/plak_cli/__init__.py": "x = 2\n"})
        commands.prepare(released.handle, DAY, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        return released

    def test_what_prepare_writes_passes(self, prepared):
        assert commands.verify_staged(prepared.handle, TAG) == []

    def test_a_release_prepared_yesterday_still_passes(self, prepared, monkeypatch):
        """Midnight between the two jobs."""
        monkeypatch.setattr(commands, "today", lambda: date(2026, 10, 2))
        assert commands.verify_staged(prepared.handle, TAG) == []

    @pytest.mark.parametrize("now", [date(2026, 10, 3), date(2026, 9, 30)], ids=["two-days-later", "tomorrow"])
    def test_a_tag_dated_outside_today_and_yesterday_is_refused(self, prepared, monkeypatch, now):
        """The tag comes from the job that ran the dependencies; one dated
        at will is refused before anything else is looked at."""
        monkeypatch.setattr(commands, "today", lambda: now)
        assert commands.verify_staged(prepared.handle, TAG) == [
            f"{TAG} is not dated today or yesterday ({now.isoformat()})."
        ]

    def test_a_tag_behind_the_newest_one_is_refused(self, prepared, monkeypatch):
        monkeypatch.setattr(commands, "today", lambda: date(2026, 8, 1))
        assert commands.verify_staged(prepared.handle, "v2026.8.1") == ["v2026.8.1 is not newer than v2026.9.1."]

    @pytest.mark.parametrize(
        "path",
        ["CHANGELOG.md", "publiccode.yml", "backend/uv.lock", "frontend/package.json", f"{NOTES}2026.10.1.en.md"],
    )
    def test_a_file_the_release_writes_has_to_be_staged(self, prepared, path):
        """A patch that leaves a version or the notes behind is not the
        release either."""
        prepared.git("reset", "-q", "--", path)
        assert commands.verify_staged(prepared.handle, TAG) == [f"{path} is missing from the release."]

    def test_the_cli_and_plugin_files_are_only_written_when_they_changed(self, released):
        released.commit({**FRAGMENT, "backend/src/toon/app.py": "app = 2\n"})
        commands.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert "cli/pyproject.toml" not in commands._staged_modes(released.handle)
        assert commands.verify_staged(released.handle, TAG) == []

    def test_a_fragment_that_is_not_a_plain_file_is_refused(self, released):
        released.commit(FRAGMENT)
        os.symlink("../README.md", released.path / "changelog.d" / "link.added.md")
        released.git("add", "-A")
        released.git("update-index", "--chmod=+x", "changelog.d/something-new.added.md")
        released.git("commit", "-q", "-m", "Mode")
        released.write({"Makefile": "all:\n"})
        released.git("add", "Makefile")
        assert commands.verify_staged(released.handle, TAG) == [
            "changelog.d/link.added.md is not a plain file.",
            "changelog.d/something-new.added.md is not a plain file.",
        ]

    def test_the_patch_survives_the_hand_over_to_a_clean_checkout(self, prepared, tmp_path):
        """What the workflow does between its two jobs: the staged release as
        a patch, applied to a fresh clone of the same commit."""
        patch = tmp_path / "release.patch"
        patch.write_text(prepared.git("diff", "--cached", "--binary", "--no-renames"), encoding="utf-8")
        clean = Repo(tmp_path / "clean")
        subprocess.run(  # noqa: S603
            ["git", "clone", "-q", str(prepared.path), str(clean.path)],  # noqa: S607
            check=True,
            capture_output=True,
        )
        clean.git("apply", "--cached", "--binary", str(patch))
        assert commands.verify_staged(clean.handle, TAG) == []
        staged = clean.git("diff", "--cached", "--name-only", "--no-renames").split()
        assert f"{NOTES}2026.10.1.nl.md" in staged
        assert "changelog.d/x.note.nl.md" in staged

    def test_main_says_so(self, prepared, capsys):
        assert release.main(["verify-staged", "--tag", TAG]) == 0
        assert capsys.readouterr().out == f"The index holds the release for {TAG} and nothing else.\n"

    @pytest.mark.parametrize(
        ("path", "text", "problem"),
        [
            (".github/workflows/x.yml", "on: push\n", ".github/workflows/x.yml is not a file a release writes."),
            ("backend/src/toon/app.py", "import os\n", "backend/src/toon/app.py is not a file a release writes."),
            (
                "backend/uv.lock",
                BACKEND_LOCK.replace("version = 1\n", 'version = 1\n\n[[package]]\nname = "evil"\n'),
                "backend/uv.lock is not what the release writes for v2026.10.1.",
            ),
            (
                "backend/src/toon/main.py",
                MAIN.replace("app = 1", "app = __import__('os')"),
                "backend/src/toon/main.py is not what the release writes for v2026.10.1.",
            ),
            ("CHANGELOG.md", changelog_text("## [2026.10.1]\n\n- Something else."), "CHANGELOG.md is not"),
            (
                "scripts/release.py",
                "def verify_staged(*a): return []\n",
                "scripts/release.py is not a file a release writes.",
            ),
            (
                "cli/pyproject.toml",
                versions.set_pyproject_version(PYPROJECT, "9.9.9"),
                "cli/pyproject.toml is not what the release writes for v2026.10.1.",
            ),
            (
                "backend/pyproject.toml",
                BACKEND_PYPROJECT.replace('requires = ["hatchling"]', 'requires = ["hatchling"]\nversion = "x"'),
                "backend/pyproject.toml is not what the release writes for v2026.10.1.",
            ),
            (
                "backend/src/toon/main.py",
                MAIN.replace("1.0.0", "1.2.0"),
                "backend/src/toon/main.py is not what the release writes for v2026.10.1.",
            ),
            (
                f"{NOTES}2026.10.1.nl.md",
                "<script>x</script>\n",
                f"{NOTES}2026.10.1.nl.md is not what the release writes for v2026.10.1.",
            ),
            (f"{NOTES}2026.9.2.en.md", "New.\n", f"{NOTES}2026.9.2.en.md is not a file a release writes."),
            ("changelog.d/evil.added.md", "- Evil.\n", "changelog.d/evil.added.md is not a file a release writes."),
            (
                "changelog.d/something-new.added.md",
                "- Edited, not removed.\n",
                "changelog.d/something-new.added.md is a fragment the release removes.",
            ),
        ],
        ids=[
            "workflow",
            "code",
            "lock-dependency",
            "code-next-to-version",
            "changelog",
            "the-validator",
            "other-version",
            "other-version-line",
            "api-version-not-the-schemas",
            "note-text",
            "note-version",
            "new-fragment",
            "fragment-kept",
        ],
    )
    def test_anything_beyond_the_release_is_refused(self, prepared, path, text, problem):
        prepared.write({path: text})
        prepared.git("add", "--", path)
        problems = commands.verify_staged(prepared.handle, TAG)
        assert len(problems) == 1 and problems[0].startswith(problem), problems

    def test_a_fragment_the_patch_leaves_in_place_is_refused(self, prepared):
        prepared.git("reset", "-q", "--", "changelog.d/x.note.nl.md")
        assert commands.verify_staged(prepared.handle, TAG) == [
            "changelog.d/x.note.nl.md is a fragment the release removes."
        ]

    def test_a_note_written_over_an_existing_one_is_refused(self, released):
        """prepare refuses when the version's note exists, so a staged one
        is not its work, whatever its text."""
        existing = {NOTES + "2026.10.1.nl.md": "a\n", NOTES + "2026.10.1.en.md": "b\n"}
        released.commit({**FRAGMENT, **NOTE_FRAGMENTS, **existing})
        released.git("rm", "-q", "--cached", *FRAGMENT, *NOTE_FRAGMENTS)
        released.write({NOTES + "2026.10.1.en.md": "New.\n"})
        released.git("add", "--", NOTES + "2026.10.1.en.md")
        problems = commands.verify_staged(released.handle, TAG)
        assert problems[0] == f"{NOTES}2026.10.1.en.md is not what the release writes for v2026.10.1."

    def test_an_executable_or_a_symlink_is_refused(self, prepared):
        """Same text, other kind of file: what the release writes is plain."""
        prepared.git("update-index", "--chmod=+x", "publiccode.yml")
        (prepared.path / "CHANGELOG.md").unlink()
        (prepared.path / "CHANGELOG.md").symlink_to("README.md")
        prepared.git("add", "CHANGELOG.md")
        assert sorted(commands.verify_staged(prepared.handle, TAG)) == [
            "CHANGELOG.md is not staged as a plain file.",
            "publiccode.yml is not staged as a plain file.",
        ]

    def test_the_schema_has_to_declare_the_version_api_version_gets(self, prepared):
        """API_VERSION follows the staged schema; one without that line
        staged has to match what the code has."""
        prepared.git("reset", "-q", "--", "backend/src/toon/main.py")
        assert commands.verify_staged(prepared.handle, TAG) == [
            "backend/src/toon/main.py is not what the release writes for v2026.10.1."
        ]

    def test_a_schema_without_a_semver_version_is_refused(self, prepared):
        prepared.write({"backend/openapi.json": spec_with("one")})
        prepared.git("add", "backend/openapi.json")
        problems = commands.verify_staged(prepared.handle, TAG)
        assert problems[0] == "backend/openapi.json: info.version 'one' is not MAJOR.MINOR.PATCH."

    def test_a_file_the_release_removes_has_to_be_a_fragment(self, prepared):
        prepared.git("rm", "-q", "--cached", "README.md")
        assert commands.verify_staged(prepared.handle, TAG) == ["README.md is not a file a release writes."]

    def test_a_version_file_that_disappears_is_refused(self, prepared):
        prepared.git("rm", "-q", "--cached", "publiccode.yml")
        assert commands.verify_staged(prepared.handle, TAG) == [
            "publiccode.yml is not what the release writes for v2026.10.1."
        ]

    def test_api_version_moves_only_with_the_schema(self, prepared):
        """The schema unstaged, API_VERSION still raised: refused."""
        prepared.git("reset", "-q", "--", "backend/openapi.json")
        assert commands.verify_staged(prepared.handle, TAG) == [
            "backend/src/toon/main.py is not what the release writes for v2026.10.1."
        ]

    def test_a_file_prepare_could_not_have_written_is_refused(self, released):
        """HEAD's publiccode.yml has no version lines, so prepare would have
        refused; a staged one is not its work."""
        released.commit({**FRAGMENT, "publiccode.yml": "name: Plak\n"})
        released.git("rm", "-q", "--cached", *FRAGMENT)
        released.write({"publiccode.yml": 'name: Plak\nsoftwareVersion: "2026.10.1"\n'})
        released.git("add", "publiccode.yml")
        problems = commands.verify_staged(released.handle, TAG)
        assert problems[0] == "publiccode.yml is not what the release writes for v2026.10.1."

    def test_a_head_prepare_would_refuse_is_refused(self, released):
        """Without entries at HEAD there is no release to compare with."""
        released.write({"Makefile": "all:\n"})
        released.git("add", "Makefile")
        assert commands.verify_staged(released.handle, TAG) == [
            "Nothing to release: changelog.d/ has no entries, and they are the release notes."
        ]
        released.commit({"changelog.d/x.improved.md": "- x\n"})
        released.write({"Makefile": "all: build\n"})
        released.git("add", "Makefile")
        [problem] = commands.verify_staged(released.handle, TAG)
        assert problem.startswith("changelog.d/x.improved.md is not <slug>")

    def test_the_tag_has_to_be_the_one_prepared(self, prepared, monkeypatch):
        """Another tag gives another changelog section, and notes named
        after another version."""
        monkeypatch.setattr(commands, "today", lambda: date(2026, 10, 2))
        problems = commands.verify_staged(prepared.handle, "v2026.10.2")
        assert "CHANGELOG.md is not what the release writes for v2026.10.2." in problems
        assert f"{NOTES}2026.10.1.nl.md is not a file a release writes." in problems

    def test_nothing_staged_or_no_calver_tag_is_refused(self, released):
        assert commands.verify_staged(released.handle, TAG) == ["Nothing is staged to release."]
        assert commands.verify_staged(released.handle, "v2026.1")[0].startswith("'v2026.1' is not a CalVer tag")

    def test_main_fails_with_every_problem(self, prepared, capsys):
        prepared.write({"Makefile": "all:\n"})
        prepared.git("add", "Makefile")
        assert release.main(["verify-staged", "--tag", TAG]) == 1
        assert "error: Makefile is not a file a release writes." in capsys.readouterr().err


class TestApiCheck:
    def test_without_a_released_schema_there_is_nothing_to_hold_to(self, repo):
        result = commands.api_check(repo.handle, SPEC, NO_CHANGES)
        assert (result.errors, result.comment) == ([], "")
        assert result.notice == (
            "No backend/openapi.json at any tag yet: the next release writes it, and checks start there."
        )

    def test_a_hand_made_tag_without_a_schema_says_so(self, repo):
        repo.git("tag", "v2026.9.1")
        assert "at v2026.9.1 yet" in commands.api_check(repo.handle, SPEC, NO_CHANGES).notice

    def test_an_unchanged_contract_passes_without_a_comment(self, released):
        result = commands.api_check(released.handle, SPEC, NO_CHANGES)
        assert (result.errors, result.notice, result.comment) == ([], None, "")

    def test_it_compares_with_the_newest_tag(self, released):
        released.commit(FRAGMENT)
        released.release(TAG, DAY, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        seen = []
        commands.api_check(released.handle, SPEC, lambda base, head: seen.append(base) or [])
        assert json.loads(seen[0])["info"]["version"] == "1.1.0"

    def test_an_addition_passes_and_says_what_the_next_release_makes_of_it(self, released):
        result = commands.api_check(released.handle, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        assert result.errors == []
        assert result.comment == (
            "<!-- toon-api-check -->\n**API:** changes against the contract of v2026.9.1.\n\n"
            "- `GET /b`: endpoint added\n\nThe next release makes it API 1.1.0.\n"
        )

    def test_a_breaking_change_without_a_new_major_fails(self, released):
        removed = [change(3, "api path removed", "/a")]
        result = commands.api_check(released.handle, spec_with(paths=()), lambda b, h: removed)
        assert result.errors[0] == "Breaking API change: GET /a: api path removed."
        assert result.comment.endswith("- `GET /a`: api path removed\n\nThis cannot be released as it is.\n")

    def test_a_breaking_change_with_a_new_major_passes(self, released):
        result = commands.api_check(released.handle, spec_with("2.0.0", paths=()), lambda b, h: [change(3)])
        assert result.errors == []
        assert result.comment.endswith("The next release makes it API 2.0.0.\n")

    @pytest.fixture
    def release_pr(self, released) -> Repo:
        """A release prepared with an addition to the API, committed and not
        tagged: the release pull request, or its commit in the merge queue."""
        released.commit(FRAGMENT)
        released.prepare(TAG, DAY, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        return released

    def test_a_release_that_ships_the_schema_of_its_code_passes(self, release_pr):
        result = commands.api_check(release_pr.handle, spec_with("1.1.0", paths=("/a", "/b")), lambda b, h: [change(1)])
        assert result.errors == []

    def test_an_edit_in_the_release_pull_request_is_still_the_release(self, release_pr):
        release_pr.commit({"CHANGELOG.md": release_pr.read("CHANGELOG.md").replace("Something new", "Something newer")})
        spec = spec_with("1.1.0", paths=("/a", "/b", "/c"))
        result = commands.api_check(release_pr.handle, spec, lambda b, h: [change(1), change(1, path="/c")])
        assert result.errors == [commands.STALE_SCHEMA]

    def test_a_change_behind_the_release_is_the_next_releases(self, release_pr):
        """An API pull request in the queue after the release, or checked
        between its merge and its tag: not this release's schema to ship."""
        release_pr.commit({"backend/src/toon/app.py": "route = '/c'\n"})
        spec = spec_with("1.1.0", paths=("/a", "/b", "/c"))
        result = commands.api_check(release_pr.handle, spec, lambda b, h: [change(1), change(1, path="/c")])
        assert result.errors == []
        assert "The next release makes it API 1.1.0." in result.comment

    def test_a_release_whose_api_changed_after_it_was_prepared_fails(self, release_pr):
        """An API pull request merged between preparing and merging the
        release: the queue rebases the release on top of it, so the schema
        the release commit carries is no longer the one its code prints."""
        spec = spec_with("1.1.0", paths=("/a", "/b", "/c"))
        result = commands.api_check(release_pr.handle, spec, lambda b, h: [change(1), change(1, path="/c")])
        assert result.errors == [
            "backend/openapi.json is not the schema this release ships: the API changed after the release "
            "was prepared; prepare it again."
        ]

    @pytest.mark.parametrize("main", [MAIN, "app = 1\n"], ids=["old-version", "no-line"])
    def test_a_release_whose_code_does_not_carry_the_api_version_fails(self, release_pr, main):
        release_pr.commit({"backend/src/toon/main.py": main})
        result = commands.api_check(release_pr.handle, spec_with("1.0.0", paths=("/a", "/b")), lambda b, h: [change(1)])
        assert result.errors == [commands.STALE_SCHEMA]

    def test_a_release_with_a_breaking_change_reports_only_that(self, release_pr):
        result = commands.api_check(release_pr.handle, spec_with("1.1.0", paths=()), lambda b, h: [change(3)])
        assert result.errors[0].startswith("Breaking API change")
        assert commands.STALE_SCHEMA not in result.errors

    def test_main_runs_oasdiff_and_fails_on_a_breaking_change(self, released, tmp_path, monkeypatch, capsys):
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        (tmp_path / "head.json").write_text(spec_with(paths=()), encoding="utf-8")
        binary = fake_oasdiff(tmp_path, json.dumps([change(3, "api path removed", "/a")]))
        comment = tmp_path / "comment.md"
        args = ["api-check", "--spec", str(tmp_path / "head.json"), "--oasdiff", binary, "--comment-file", str(comment)]

        assert release.main(args) == 1
        out = capsys.readouterr().out
        assert "::error title=API contract::Breaking API change: GET /a: api path removed." in out
        assert summary.read_text(encoding="utf-8").startswith("## API contract\n\n- Breaking API change")
        assert comment.read_text(encoding="utf-8").startswith("<!-- toon-api-check -->\n")

    def test_main_passes_an_unchanged_contract_and_empties_the_comment(self, released, tmp_path, capsys):
        (tmp_path / "head.json").write_text(SPEC, encoding="utf-8")
        comment = tmp_path / "comment.md"
        comment.write_text("old", encoding="utf-8")
        args = ["api-check", "--spec", str(tmp_path / "head.json"), "--oasdiff", fake_oasdiff(tmp_path)]
        assert release.main([*args, "--comment-file", str(comment)]) == 0
        assert capsys.readouterr().out == "The API contract holds.\n"
        assert comment.read_text(encoding="utf-8") == ""

    def test_main_without_a_released_schema_gives_a_notice(self, repo, tmp_path, capsys):
        (tmp_path / "head.json").write_text(SPEC, encoding="utf-8")
        assert release.main(["api-check", "--spec", str(tmp_path / "head.json"), "--oasdiff", "unused"]) == 0
        assert capsys.readouterr().out.startswith("::notice title=API contract::No backend/openapi.json")


class TestCheck:
    def test_a_shipped_change_with_a_fragment_is_in_order(self, branch):
        branch.commit({"backend/src/toon/main.py": "app = 2\n", **FRAGMENT})
        assert commands.check(branch.handle, "beta") == commands.CheckResult()

    def test_a_shipped_change_without_a_fragment_warns(self, branch):
        branch.commit({"backend/src/toon/main.py": "app = 2\n"})
        result = commands.check(branch.handle, "beta")
        assert result.errors == []
        assert result.warning == commands.WARNING

    def test_a_fragment_removed_is_no_entry(self, released):
        """What counts is a fragment this branch adds or edits, not one it
        takes away."""
        released.commit(FRAGMENT)
        released.git("switch", "-q", "-c", "feature")
        released.git("rm", "-q", "changelog.d/something-new.added.md")
        released.commit({"backend/src/toon/main.py": "app = 2\n"})
        assert commands.check(released.handle, "beta").warning == commands.WARNING

    @pytest.mark.parametrize(
        "body",
        ["Bump a dependency.\n\nNo changelog entry: tooling only", "No changelog entry:  refactor, no effect"],
    )
    def test_the_description_can_say_why_there_is_no_entry(self, branch, body):
        branch.commit({"backend/src/toon/main.py": "app = 2\n"})
        assert commands.check(branch.handle, "beta", body).warning is None

    @pytest.mark.parametrize("body", ["No changelog entry:", "No changelog entry:   ", "See: No changelog entry: x"])
    def test_an_opt_out_without_a_reason_or_mid_line_does_not_count(self, branch, body):
        branch.commit({"backend/src/toon/main.py": "app = 2\n"})
        assert commands.check(branch.handle, "beta", body).warning == commands.WARNING

    def test_a_change_outside_the_shipped_paths_does_not_warn(self, branch):
        branch.commit({"docs/x.md": "docs\n", "backend/tests/test_x.py": "def test(): pass\n"})
        assert commands.check(branch.handle, "beta") == commands.CheckResult()

    def test_only_the_branch_own_changes_count(self, branch):
        """A shipped change that landed on beta after the branch started is
        beta's, and does not ask this branch for an entry."""
        branch.git("switch", "-q", "beta")
        branch.commit({"backend/src/toon/main.py": "app = 2\n", **FRAGMENT})
        branch.git("switch", "-q", "feature")
        branch.commit({"README.md": "better\n"})
        assert commands.check(branch.handle, "beta").warning is None

    def test_the_pull_request_that_adds_the_changelog_does_not_warn(self, tmp_path):
        repo = Repo(tmp_path / "fresh")
        repo.path.mkdir()
        repo.git("init", "-q", "-b", "beta")
        repo.commit({"README.md": "x\n"})
        repo.git("switch", "-q", "-c", "feature")
        repo.commit({"CHANGELOG.md": changelog_text(), "backend/src/toon/main.py": "app = 1\n", **FRAGMENT})
        assert commands.check(repo.handle, "beta") == commands.CheckResult()

    @pytest.mark.parametrize(
        ("files", "reason"),
        [
            ({"changelog.d/x.improved.md": "- x\n"}, "changelog.d/x.improved.md is not <slug>"),
            ({"changelog.d/x.added.md": "### Added\n\n- x\n"}, "'### Added' is not an entry"),
            ({"changelog.d/x.note.nl.md": "Nieuw\n"}, "x.note.nl.md has no x.note.en.md"),
        ],
        ids=["name", "content", "unpaired-note"],
    )
    def test_a_malformed_fragment_fails(self, branch, files, reason):
        branch.commit({"backend/src/toon/main.py": "app = 2\n", **files})
        [error] = commands.check(branch.handle, "beta").errors
        assert reason in error

    def test_a_fragment_that_is_a_symlink_fails(self, branch):
        (branch.path / "changelog.d").mkdir()
        os.symlink("../README.md", branch.path / "changelog.d" / "link.added.md")
        branch.commit(FRAGMENT)
        assert commands.check(branch.handle, "beta").errors == ["changelog.d/link.added.md is not a plain file."]

    def test_an_entry_under_unreleased_fails(self, branch):
        branch.commit({"CHANGELOG.md": changelog_text(RELEASED_SECTION, unreleased=ENTRY)})
        [error] = commands.check(branch.handle, "beta").errors
        assert error.startswith("CHANGELOG.md: an entry under [Unreleased] belongs in a fragment")

    def test_a_missing_changelog_fails(self, branch):
        branch.remove("CHANGELOG.md")
        assert commands.check(branch.handle, "beta").errors == ["CHANGELOG.md is missing."]

    @pytest.mark.parametrize(
        "edit",
        [
            lambda text: text.replace("- Something new.", "- Something newer."),
            lambda text: text.replace("## [2026.9.1]", "## [2026.9.2]"),
            lambda text: text[: text.index("## [2026.9.1]")],
        ],
        ids=["body", "renamed", "removed"],
    )
    def test_editing_a_released_section_fails(self, branch, edit):
        """With the links following the edit, so only the freeze is left."""
        branch.commit({"CHANGELOG.md": changelog.with_links(edit(branch.read("CHANGELOG.md")))})
        errors = commands.check(branch.handle, "beta").errors
        assert errors[0] == (
            "CHANGELOG.md: [2026.9.1] was released as v2026.9.1 and is frozen; "
            "describe the change in a fragment under changelog.d/ instead."
        )

    def test_a_section_for_an_existing_tag_is_no_release(self, repo):
        """A version tagged by hand cannot get its section afterwards: the
        section that comes in is a release, and that tag is taken."""
        repo.git("tag", "v2026.9.1")
        repo.git("switch", "-q", "-c", "feature")
        repo.commit({"CHANGELOG.md": changelog_text("## [2026.9.1]\n\nWhat ran then.\n\n- x")})
        assert "v2026.9.1 is not newer than v2026.9.1." in commands.check(repo.handle, "beta").errors

    def test_a_section_without_a_tag_is_not_frozen(self, released):
        """The release pull request may be edited until it merges."""
        released.commit({"changelog.d/other.fixed.md": "- Other thing.\n"})
        released.prepare(TAG)
        released.git("switch", "-q", "-c", "reword")
        released.commit({"CHANGELOG.md": released.read("CHANGELOG.md").replace("Other thing", "Other things")})
        assert commands.check(released.handle, "beta").errors == []

    @pytest.fixture
    def release_pr(self, released) -> Repo:
        """The fragments on beta, the release prepared on its own branch."""
        released.commit({**FRAGMENT, **NOTE_FRAGMENTS, "frontend/src/App.vue": "x\n"})
        released.git("switch", "-q", "-c", f"release/{TAG}")
        released.prepare(TAG)
        return released

    def test_a_release_pull_request_passes_without_a_warning(self, release_pr):
        """It changes shipped paths and adds no fragment: that is the point."""
        assert commands.check(release_pr.handle, "beta") == commands.CheckResult()

    def test_a_release_pull_request_with_a_fragment_left_fails(self, release_pr):
        """A fragment that merged between preparing and merging: the release
        no longer says everything it ships."""
        release_pr.commit({"changelog.d/late.fixed.md": "- Late.\n"})
        assert commands.check(release_pr.handle, "beta").errors == [
            "changelog.d/late.fixed.md is still there: a release removes every fragment."
        ]

    def test_a_release_pull_request_with_a_wrong_version_fails(self, release_pr):
        release_pr.commit({"publiccode.yml": PUBLICCODE})
        assert commands.check(release_pr.handle, "beta").errors == [
            "publiccode.yml at HEAD does not carry version 2026.10.1."
        ]

    @pytest.mark.parametrize(
        ("sections", "added"),
        [
            (f"{RELEASED_SECTION}\n\n## [2026.8.1]\n\n- Old.", "[2026.8.1]"),
            (f"## [2026.10.2]\n\n- b\n\n## [2026.10.1]\n\n- a\n\n{RELEASED_SECTION}", "[2026.10.2], [2026.10.1]"),
        ],
        ids=["below", "two"],
    )
    def test_a_release_adds_exactly_one_section_on_top(self, branch, sections, added):
        branch.commit({"CHANGELOG.md": changelog_text(sections)})
        assert commands.check(branch.handle, "beta").errors == [
            f"CHANGELOG.md: a release adds one section, on top; this adds {added}."
        ]

    def test_a_new_section_with_a_malformed_heading_is_reported_once(self, branch):
        branch.commit({"CHANGELOG.md": changelog_text(f"## [2026.10.01]\n\n- a\n\n{RELEASED_SECTION}")})
        [error] = commands.check(branch.handle, "beta").errors
        assert "'v2026.10.01' has a leading zero" in error

    @pytest.mark.parametrize(
        ("files", "reason"),
        [
            ({"2026.9.1.nl.md": "Nieuw\n"}, "What's new note comes in Dutch and English"),
            ({"unreleased.nl.md": "Nieuw\n", "unreleased.en.md": "New\n"}, "is not <version>.nl.md or <version>.en.md"),
        ],
        ids=["one-language", "not-a-version"],
    )
    def test_a_note_that_is_not_a_pair_for_a_release_fails(self, branch, files, reason):
        branch.commit({NOTES + name: text for name, text in files.items()})
        errors = commands.check(branch.handle, "beta").errors
        assert reason in errors[0]

    @pytest.mark.parametrize("path", ["frontend/src/App.vue", "cli/plak_cli/__init__.py"])
    def test_a_member_facing_change_without_a_note_gets_a_hint(self, branch, path):
        branch.commit({path: "changed\n", **FRAGMENT})
        result = commands.check(branch.handle, "beta")
        assert (result.errors, result.warning, result.hint) == ([], None, commands.HINT)

    def test_a_note_fragment_removed_is_no_note(self, released):
        released.commit(NOTE_FRAGMENTS)
        released.git("switch", "-q", "-c", "feature")
        released.git("rm", "-q", "changelog.d/x.note.nl.md", "changelog.d/x.note.en.md")
        released.commit({"frontend/src/App.vue": "x\n", **FRAGMENT})
        assert commands.check(released.handle, "beta").hint == commands.HINT

    @pytest.mark.parametrize(
        "files",
        [
            {"frontend/src/App.vue": "x\n", **NOTE_FRAGMENTS},
            {"frontend/src/App.test.ts": "x\n"},
            {"backend/src/toon/main.py": "app = 2\n"},
            {NOTES + "2026.9.1.nl.md": "a\n", NOTES + "2026.9.1.en.md": "b\n"},
        ],
        ids=["with-note", "test-only", "backend", "notes-only"],
    )
    def test_no_hint_otherwise(self, branch, files):
        branch.commit({**files, **FRAGMENT})
        assert commands.check(branch.handle, "beta").hint is None

    def test_hard_only_skips_the_warning_and_the_hint(self, branch):
        branch.commit({"frontend/src/App.vue": "x\n", "changelog.d/x.improved.md": "- x\n"})
        result = commands.check(branch.handle, "beta", hard_only=True)
        assert len(result.errors) == 1
        assert (result.warning, result.hint) == (None, None)


class TestCheckCommand:
    def test_errors_fail_with_annotations_and_a_summary(self, branch, monkeypatch, tmp_path, capsys):
        branch.commit({"changelog.d/x.improved.md": "- x\n"})
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        assert release.main(["check", "--base", "beta"]) == 1
        assert capsys.readouterr().out.startswith("::error title=Changelog::changelog.d/x.improved.md is not <slug>")
        assert summary.read_text().startswith("## Changelog\n\n- **Error:** ")

    def test_a_warning_passes_and_fills_the_comment(self, branch, monkeypatch, tmp_path, capsys):
        branch.commit({"frontend/src/App.vue": "x\n"})
        summary, comment = tmp_path / "summary.md", tmp_path / "comment.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        assert release.main(["check", "--base", "beta", "--comment-file", str(comment)]) == 0
        out = capsys.readouterr().out
        assert f"::warning title=Changelog::{commands.WARNING}\n" in out
        assert f"::notice title=What's new::{commands.HINT}\n" in out
        assert comment.read_text() == f"{commands.COMMENT_MARKER}\n**Changelog:** {commands.WARNING}\n"
        assert "- **Warning:** " in summary.read_text()
        assert "- **Hint:** " in summary.read_text()

    def test_the_description_file_clears_the_warning_and_empties_the_comment(self, branch, tmp_path, capsys):
        branch.commit({"backend/src/toon/main.py": "app = 2\n"})
        body, comment = tmp_path / "body.md", tmp_path / "comment.md"
        body.write_text("Chore.\n\nNo changelog entry: lockfile refresh\n")
        comment.write_text("stale")

        args = ["check", "--base", "beta", "--body-file", str(body), "--comment-file", str(comment)]
        assert release.main(args) == 0
        assert capsys.readouterr().out == "CHANGELOG.md, changelog.d/ and the What's new notes are in order.\n"
        assert comment.read_text() == ""

    def test_the_merge_queue_runs_only_the_hard_checks(self, branch, capsys):
        branch.commit({"frontend/src/App.vue": "x\n"})
        assert release.main(["check", "--base", "beta", "--hard-only"]) == 0
        assert "::warning" not in capsys.readouterr().out

    def test_an_unknown_base_fails(self, branch, capsys):
        assert release.main(["check", "--base", "nope"]) == 1
        assert "error: git merge-base nope HEAD" in capsys.readouterr().err

    def test_annotations_escape_what_would_end_them(self):
        assert commands._annotation("error", "T", "50%\nnext\r") == "::error title=T::50%25%0Anext%0D"


class TestHook:
    def test_a_pull_request_without_a_fragment_is_denied(self, pushed):
        decision = json.loads(commands.hook(_event(pushed, "gh pr create --title x --body 'Fix it'")))
        assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Shipped paths changed without a changelog fragment" in reason
        assert "changelog.d/<slug>.<kind>.md" in reason
        assert "changelog skill" in reason
        assert "No changelog entry: <reason>" in reason

    def test_a_pull_request_with_a_fragment_passes(self, pushed):
        pushed.commit(FRAGMENT)
        assert commands.hook(_event(pushed, "gh pr create --fill")) is None

    def test_a_release_pull_request_passes(self, released):
        """It changes shipped paths and adds no fragment: that is what a
        release does, as check() knows too."""
        released.commit(FRAGMENT)
        released.git("update-ref", "refs/remotes/origin/beta", "beta")
        released.git("switch", "-q", "-c", f"release/{TAG}")
        released.prepare(TAG)
        assert commands.hook(_event(released, "gh pr create --fill")) is None

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr create --title x --body \"No changelog entry: docs only\"",
            "gh pr create --title x --body-file - <<'EOF'\nSummary.\n\nNo changelog entry: tests only\nEOF",
        ],
        ids=["inline", "heredoc"],
    )
    def test_an_opt_out_in_the_command_passes(self, pushed, command):
        assert commands.hook(_event(pushed, command)) is None

    @pytest.mark.parametrize("flag", ["--body-file {}", "--body-file={}", "-F {}"])
    def test_an_opt_out_in_the_body_file_passes(self, pushed, flag):
        (pushed.path / "body.md").write_text("Summary.\n\nNo changelog entry: CI only\n")
        assert commands.hook(_event(pushed, "gh pr create --title x " + flag.format("body.md"))) is None

    @pytest.mark.parametrize(
        "tail",
        ["--body-file body.md", "--body-file missing.md", "--body-file", "--body 'unbalanced"],
        ids=["no-opt-out", "missing-file", "no-file-name", "unparseable"],
    )
    def test_a_body_that_does_not_opt_out_is_denied(self, pushed, tail):
        (pushed.path / "body.md").write_text("Summary only.\n")
        assert commands.hook(_event(pushed, "gh pr create --title x " + tail)) is not None

    @pytest.mark.parametrize(
        "stdin",
        ["not json", "{}", '{"tool_input": null}', "[]", json.dumps({"tool_input": {"command": "gh pr list"}})],
    )
    def test_anything_but_a_pull_request_creation_passes(self, pushed, stdin):
        assert commands.hook(stdin) is None

    def test_without_origin_beta_it_passes_and_says_why(self, branch, capsys):
        branch.commit({"backend/src/toon/main.py": "app = 2\n"})
        assert commands.hook(_event(branch, "gh pr create --fill")) is None
        assert capsys.readouterr().err.startswith("changelog hook: skipped, git merge-base origin/beta HEAD")

    def test_main_prints_the_decision(self, pushed, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(_event(pushed, "gh pr create --fill")))
        assert release.main(["hook"]) == 0
        assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_main_is_silent_when_it_allows(self, pushed, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(_event(pushed, "git status")))
        assert release.main(["hook"]) == 0
        assert capsys.readouterr().out == ""

    def test_the_project_settings_run_it_before_gh_pr_create(self):
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        [entry] = settings["hooks"]["PreToolUse"]
        assert entry["matcher"] == "Bash"
        [handler] = entry["hooks"]
        assert handler["type"] == "command"
        assert handler["if"] == "Bash(gh pr create *)"
        assert '"${CLAUDE_PROJECT_DIR}/scripts/release.py" hook' in handler["command"]


class TestRefreshingTheReleasePullRequest:
    """What prepare-release.yml asks on a push to beta: is a release waiting
    for its tag, and which tag does the refreshed pull request get."""

    def test_nothing_is_pending_when_the_newest_section_is_tagged(self, released):
        assert commands.pending_release(released.handle) == ""

    def test_a_merged_release_without_its_tag_is_pending(self, released):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        assert commands.pending_release(released.handle) == "2026.10.1"

    def test_main_prints_the_pending_version_and_writes_the_step_output(self, released, monkeypatch, tmp_path, capsys):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        output = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        assert release.main(["pending"]) == 0
        assert capsys.readouterr().out == "version=2026.10.1\n"
        assert output.read_text() == "version=2026.10.1\n"

    def test_main_prints_an_empty_version_when_nothing_is_pending(self, released, capsys):
        assert release.main(["pending"]) == 0
        assert capsys.readouterr().out == "version=\n"

    def test_the_tag_stays_while_verify_staged_would_still_take_it(self, released, monkeypatch):
        monkeypatch.setattr(commands, "today", lambda: date(2026, 10, 2))
        assert commands.refresh_tag(released.handle, "release/v2026.10.1") == "v2026.10.1"
        assert commands.refresh_tag(released.handle, "release/v2026.10.2") == "v2026.10.2"

    @pytest.mark.parametrize(
        ("branch", "now"),
        [
            ("release/v2026.10.1", date(2026, 10, 3)),
            ("release/v2026.8.1", date(2026, 10, 3)),
            ("release/v2026.10.01", date(2026, 10, 3)),
        ],
        ids=["two-days-old", "behind-the-newest-tag", "not-calver"],
    )
    def test_a_stale_tag_gives_way_to_the_next_one(self, released, monkeypatch, branch, now):
        monkeypatch.setattr(commands, "today", lambda: now)
        assert commands.refresh_tag(released.handle, branch) == "v2026.10.3"

    def test_a_branch_that_is_no_release_branch_is_refused(self, released):
        with pytest.raises(errors.ReleaseError, match="'feature/x' is not a release branch"):
            commands.refresh_tag(released.handle, "feature/x")

    def test_main_prints_the_tag_and_writes_the_step_output(self, released, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(commands, "today", lambda: DAY)
        output = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        assert release.main(["refresh-tag", "--branch", "release/v2026.10.1"]) == 0
        assert capsys.readouterr().out == f"tag={TAG}\n"
        assert output.read_text() == f"tag={TAG}\n"


def pull(number, branch: str, oid: str = "a" * 40, owner: str = "DigiGilde") -> dict:
    """One entry of `gh pr list --json number,headRefName,headRefOid,headRepositoryOwner`."""
    return {"number": number, "headRefName": branch, "headRefOid": oid, "headRepositoryOwner": {"login": owner}}


class TestFindingTheReleasePullRequest:
    """Both jobs of prepare-release.yml ask the same question of what
    `gh pr list` returns; `open` acts on its own answer only."""

    def test_the_release_branch_of_this_repository_is_found(self):
        a_fork = pull(8, "release/v2026.10.1", "b" * 40, "someone")
        listing = json.dumps([pull(7, "feature/x"), a_fork, pull(9, "release/v2026.10.1")])
        assert commands.release_pull_request(listing, "DigiGilde/plak") == ("9", "v2026.10.1", "a" * 40)

    @pytest.mark.parametrize(
        "listing",
        [[], [pull(7, "feature/x")], [pull(8, "release/v2026.10.1", owner="someone")], [pull(8, "release/next")]],
        ids=["nothing-open", "no-release-branch", "a-fork", "no-calver-tag"],
    )
    def test_anything_else_is_no_release_pull_request(self, listing):
        assert commands.release_pull_request(json.dumps(listing), "DigiGilde/plak") == ("", "", "")

    @pytest.mark.parametrize(
        "entry",
        [
            pull("12; rm -rf", "release/v2026.10.1"),
            pull(12, "release/v2026.10.1", "HEAD"),
            pull(12, "release/v2026.10.1", ""),
        ],
        ids=["number", "sha-not-hex", "sha-missing"],
    )
    def test_a_number_or_sha_that_is_not_plain_is_refused(self, entry):
        """What goes into `gh pr close` and the lease has one shape."""
        with pytest.raises(errors.ReleaseError, match="has no plain number and head sha"):
            commands.release_pull_request(json.dumps([entry]), "DigiGilde/plak")

    @pytest.mark.parametrize(("listing", "reason"), [("{", "is not JSON"), ('{"a": 1}', "is not a list")])
    def test_a_listing_that_is_not_one_is_refused(self, listing, reason):
        with pytest.raises(errors.ReleaseError, match=reason):
            commands.release_pull_request(listing, "DigiGilde/plak")

    def test_main_reads_the_listing_and_writes_the_step_output(self, monkeypatch, tmp_path, capsys):
        """Without a repository: it only parses, so a checkout is not needed."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([pull(12, "release/v2026.10.1")])))
        output = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        assert release.main(["release-pr", "--repo", "DigiGilde/plak"]) == 0
        expected = f"number=12\nold_tag=v2026.10.1\nold_sha={'a' * 40}\n"
        assert capsys.readouterr().out == expected
        assert output.read_text() == expected

    def test_main_prints_empty_values_without_one(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
        assert release.main(["release-pr", "--repo", "DigiGilde/plak"]) == 0
        assert capsys.readouterr().out == "number=\nold_tag=\nold_sha=\n"
