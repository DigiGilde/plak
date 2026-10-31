"""CHANGELOG.md: sections, CalVer tags and link references (plak_release.changelog)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest
from packaging.version import Version
from release_support import DAY, ENTRY, ROOT, TAG, changelog_text, release

from plak_release import changelog, errors


class TestTags:
    @pytest.mark.parametrize("tag", ["v2026.10.1", "v2026.1.5", "v2026.10.1.1", "v2026.12.31.12"])
    def test_calver_tags_pass(self, tag):
        assert changelog.tag_error(tag) is None

    @pytest.mark.parametrize(
        ("tag", "reason"),
        [
            ("2026.10.1", "not a CalVer tag"),
            ("v2026.10", "not a CalVer tag"),
            ("v26.10.1", "not a CalVer tag"),
            ("v2026.10.1-rc1", "not a CalVer tag"),
            ("v2026.01.5", "leading zero"),
            ("v2026.10.1.01", "leading zero"),
            ("v2026.10.1.0", "counts from .1"),
            ("v2026.2.30", "not name a real date"),
            ("v2026.13.1", "not name a real date"),
        ],
    )
    def test_anything_else_is_refused_with_the_reason(self, tag, reason):
        assert reason in changelog.tag_error(tag)

    def test_the_first_release_of_a_day_has_no_suffix(self):
        assert changelog.next_tag(DAY, []) == TAG

    def test_a_second_release_the_same_day_counts_from_one(self):
        assert changelog.next_tag(DAY, [TAG]) == "v2026.10.1.1"
        assert changelog.next_tag(DAY, [TAG, "v2026.10.1.1"]) == "v2026.10.1.2"

    def test_tags_outside_the_scheme_do_not_count(self):
        assert changelog.next_tag(DAY, ["v1.0.0", "latest", "v2027.01.01"]) == TAG

    def test_tags_compare_as_numbers_not_as_text(self):
        assert changelog.newest_tag(["v2026.10.1", "v2026.9.30", "v2026.10.1.2", "v2026.10.1.10"]) == "v2026.10.1.10"
        assert changelog.newest_tag(["latest"]) is None

    def test_a_date_behind_the_newest_tag_is_refused(self):
        """Forward only: a runner with a wrong clock must not cut a tag that
        sorts before the release that is already out."""
        with pytest.raises(errors.ReleaseError, match=re.escape("not newer than v2026.10.2")):
            changelog.next_tag(DAY, ["v2026.10.2"])

    def test_the_tag_names_its_date(self):
        assert changelog.tag_date("v2026.10.1.3") == DAY

    def test_today_is_the_date_in_amsterdam_not_in_utc(self, monkeypatch):
        """Half past eleven at night UTC is already the next day here."""

        class LateEvening(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 30, 23, 30, tzinfo=UTC).astimezone(tz)

        monkeypatch.setattr(changelog, "datetime", LateEvening)
        assert changelog.today() == date(2026, 10, 1)

    def test_calver_versions_are_pep_440_and_sort_above_the_old_ones(self):
        assert Version("2026.10.1") > Version("0.3.1")
        assert Version("2026.10.1.1") > Version("2026.10.1")
        assert Version("2026.10.1") > Version("2026.9.30")


class TestChangelogLinks:
    """Keep a Changelog headings are links: `## [2026.10.1]` needs a
    `[2026.10.1]: <url>` at the bottom, or it shows its brackets."""

    BASE = "https://github.com/DigiGilde/plak"

    def test_each_version_compares_with_the_one_before_and_the_first_is_its_tag(self):
        assert changelog.changelog_links(["2026.10.4.1", "2026.10.4", "2026.9.30"]) == [
            f"[Unreleased]: {self.BASE}/compare/v2026.10.4.1...HEAD",
            f"[2026.10.4.1]: {self.BASE}/compare/v2026.10.4...v2026.10.4.1",
            f"[2026.10.4]: {self.BASE}/compare/v2026.9.30...v2026.10.4",
            f"[2026.9.30]: {self.BASE}/releases/tag/v2026.9.30",
        ]

    def test_without_a_release_there_is_nothing_to_link(self):
        """Also not [Unreleased]: it compares with the newest release, and
        there is none yet."""
        assert changelog.changelog_links([]) == []
        assert changelog.with_links("# Changelog\n\n[stale]: https://example.org\n") == "# Changelog\n"
        assert changelog.with_links(changelog_text()) == changelog_text()
        assert "[Unreleased]:" not in changelog_text()

    def test_the_links_are_written_anew_and_nothing_else_moves(self):
        stale = changelog_text("## [2026.9.1]\n\n- Old.").replace("/releases/tag/v2026.9.1", "/releases/tag/v2026.1.1")
        fresh = changelog.with_links(stale)
        assert fresh == changelog_text("## [2026.9.1]\n\n- Old.")
        assert changelog.with_links(fresh) == fresh

    def test_the_links_are_no_part_of_the_last_section(self):
        """So the oldest release's notes and its freeze ignore them."""
        sections = changelog.split_sections(changelog_text("## [2026.9.1]\n\n- Old."))
        assert sections[-1].body == "- Old."

    @pytest.mark.parametrize(
        "edit",
        [
            lambda text: text[: text.index("\n[2026.9.1]: ")] + "\n",
            lambda text: text.replace("/releases/tag/v2026.9.1", "/releases/tag/v2026.9.2"),
            lambda text: text + "[extra]: https://example.org\n",
            lambda text: text.replace("[Unreleased]: ", "[unreleased]: "),
        ],
        ids=["missing", "wrong", "extra", "unreleased-wrong"],
    )
    def test_links_that_do_not_follow_the_headings_fail_the_check(self, edit):
        text = edit(changelog_text("## [2026.9.1]\n\n- Old."))
        assert changelog.changelog_problems(text) == [
            "CHANGELOG.md: the link references at the bottom are not the ones its headings need; "
            "`release.py links` writes them."
        ]

    def test_main_writes_them(self, repo, capsys):
        (repo.path / "CHANGELOG.md").write_text(
            changelog_text("## [2026.9.1]\n\n- Old.").split("\n[2026.9.1]: ")[0] + "\n", encoding="utf-8"
        )
        assert release.main(["links"]) == 0
        assert capsys.readouterr().out == "updated CHANGELOG.md\n"
        assert repo.read("CHANGELOG.md") == changelog_text("## [2026.9.1]\n\n- Old.")


class TestChangelogStructure:
    def test_a_full_changelog_is_valid(self):
        text = changelog_text(
            "## [2026.10.1]\n\n### Added\n\n- One line,\n  wrapped onto a second.\n\n### Security\n\n- Another.\n\n"
            "## [2026.9.1]\n\nA paragraph before the list.\n\n### Fixed\n\n- Bump.\n\n## [2026.8.31.1]\n\n- Old."
        )
        assert changelog.changelog_problems(text) == []

    def test_a_changelog_without_a_release_yet_is_valid(self):
        assert changelog.changelog_problems(changelog_text()) == []

    def test_the_changelog_of_this_repository_is_valid(self):
        assert changelog.changelog_problems((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")) == []

    POINTER = "Merged but not released yet: the fragments in [changelog.d/](changelog.d/README.md)."

    def test_the_pointer_is_the_one_line_under_unreleased(self):
        assert changelog.UNRELEASED_POINTER == self.POINTER

    @pytest.mark.parametrize(
        "text",
        [
            "# Changelog\n\nNothing yet.\n",
            "# Changelog\n\n## [2026.9.1]\n\n- x\n\n[2026.9.1]: https://github.com/DigiGilde/plak/releases/tag/v2026.9.1\n",
            "# Changelog\n\n## [2026.9.1]\n\n- x\n\n## [Unreleased]\n\n" + POINTER + "\n",
        ],
        ids=["no-sections", "missing", "not-first"],
    )
    def test_unreleased_has_to_come_first(self, text):
        assert changelog.changelog_problems(text) == [
            "CHANGELOG.md: the first section has to be '## [Unreleased]', with under it: " + self.POINTER
        ]

    ENTRY_PROBLEM = "an entry under [Unreleased] belongs in a fragment under changelog.d/ (docs/releasing.md)."
    ONLY_THE_POINTER = "under [Unreleased] stands one line and nothing else: " + POINTER

    @pytest.mark.parametrize(
        ("unreleased", "problem"),
        [
            ("### Added\n\n- x", ENTRY_PROBLEM),
            ("- x\n\n" + POINTER, ENTRY_PROBLEM),
            ("", ONLY_THE_POINTER),
            ("<!-- release: hold -->\n\n" + POINTER, ONLY_THE_POINTER),
            (POINTER.rstrip("."), ONLY_THE_POINTER),
        ],
        ids=["heading-and-entry", "entry-and-pointer", "empty", "hold-marker", "reworded"],
    )
    def test_anything_but_the_pointer_under_unreleased_is_refused(self, unreleased, problem):
        """Changes wait in changelog.d/, not in the changelog; the hold marker
        is gone with the automatic release."""
        assert changelog.changelog_problems(changelog_text(unreleased=unreleased)) == [f"CHANGELOG.md: {problem}"]

    def test_a_second_unreleased_section_appears_twice(self):
        text = changelog_text("## [Unreleased]\n\n- x\n\n## [2026.9.1]\n\n- y")
        assert changelog.changelog_problems(text) == ["CHANGELOG.md: '## [Unreleased]' appears twice."]

    @pytest.mark.parametrize(
        ("heading", "reason"),
        [
            ("## 2026.9.1", "is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'"),
            ("## [2026.9.1] - 2026-09-01", "is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'"),
            ("## [2026.09.1]", "'## [2026.09.1]': 'v2026.09.1' has a leading zero"),
            ("## [2026.2.30]", "'## [2026.2.30]': 'v2026.2.30' does not name a real date"),
            ("## [1.0.0]", "'## [1.0.0]': 'v1.0.0' is not a CalVer tag"),
        ],
    )
    def test_a_malformed_heading_is_refused(self, heading, reason):
        [problem] = changelog.changelog_problems(changelog_text(f"{heading}\n\n- x"))
        assert reason in problem

    @pytest.mark.parametrize("heading", ["## [v2026.9.1] - 2026-09-01", "## [v2026.9.1]"])
    def test_the_old_heading_with_the_tag_and_a_date_is_refused(self, heading):
        """The version is the date already; the old form repeated it."""
        [problem] = changelog.changelog_problems(changelog_text(f"{heading}\n\n- x"))
        assert problem == (
            f"CHANGELOG.md: '{heading}' is the old form; write '## [2026.9.1]', "
            "the version without the v and without a date."
        )

    def test_a_version_twice_is_refused(self):
        section = "## [2026.9.1]\n\n- x\n"
        [problem] = changelog.changelog_problems(changelog_text(section + "\n" + section))
        assert "'## [2026.9.1]' appears twice" in problem

    def test_parse_raises_with_every_problem(self):
        with pytest.raises(errors.ReleaseError) as refused:
            changelog.parse_changelog(changelog_text("## 2026.9.1\n\n- x\n\n## [1.0.0]\n\n- y"))
        assert len(refused.value.problems) == 2


class TestPendingVersion:
    """The newest section whose tag does not exist is a release on its way."""

    def test_the_newest_section_without_a_tag_is_pending(self):
        sections = changelog.split_sections(changelog_text("## [2026.10.1]\n\n- x\n\n## [2026.9.1]\n\n- y"))
        assert changelog.pending_version(sections, ["v2026.9.1"]) == "2026.10.1"

    def test_a_tagged_newest_section_is_not(self):
        sections = changelog.split_sections(changelog_text("## [2026.10.1]\n\n- x\n\n## [2026.9.1]\n\n- y"))
        assert changelog.pending_version(sections, ["v2026.9.1", "v2026.10.1"]) is None

    def test_without_a_version_section_nothing_is_pending(self):
        """[Unreleased] is no release: a changelog with nothing else waits."""
        assert changelog.pending_version([], []) is None
        assert changelog.pending_version(changelog.split_sections(changelog_text()), []) is None


class TestReleaseText:
    def test_the_section_goes_below_unreleased_and_above_the_newest_version(self):
        text = changelog.release_text(changelog_text("## [2026.9.1]\n\n- Old."), "2026.10.1", ENTRY)
        assert text == changelog_text(f"## [2026.10.1]\n\n{ENTRY}\n\n## [2026.9.1]\n\n- Old.")
        assert changelog.changelog_problems(text) == []
        assert "[Unreleased]: https://github.com/DigiGilde/plak/compare/v2026.10.1...HEAD" in text

    def test_the_first_version_follows_unreleased_with_a_blank_line(self):
        text = changelog.release_text(changelog_text(), "2026.10.1", ENTRY)
        assert text == changelog_text(f"## [2026.10.1]\n\n{ENTRY}")
        assert changelog.changelog_problems(text) == []

    def test_an_empty_changelog_gets_the_section_alone(self):
        """No [Unreleased] to go under: the section stands alone, and the links
        follow the versions. The check refuses such a changelog anyway."""
        assert changelog.release_text("", "2026.10.1", "- x") == (
            "## [2026.10.1]\n\n- x\n\n"
            "[Unreleased]: https://github.com/DigiGilde/plak/compare/v2026.10.1...HEAD\n"
            "[2026.10.1]: https://github.com/DigiGilde/plak/releases/tag/v2026.10.1\n"
        )
