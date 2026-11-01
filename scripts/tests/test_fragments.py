"""The fragments under changelog.d/ (toon_release.fragments)."""

from __future__ import annotations

import pytest
from release_support import BOTH_LANGUAGES, ROOT

from toon_release import fragments, git


class TestFragments:
    FORMS = (
        "is not <slug>.<kind>.md with kind one of added, changed, deprecated, removed, fixed, security, "
        "or <slug>.note.nl.md and <slug>.note.en.md."
    )

    def test_entries_notes_and_the_readme_are_fine(self):
        files = {
            "README.md": "What goes here.\n",
            "site-id.added.md": "- One,\n  wrapped onto a second line.\n\n- Two.\n\t  tabbed continuation.\n",
            "a1.changed.md": "- x\n",
            "b.deprecated.md": "- x\n",
            "c.removed.md": "- x\n",
            "d.fixed.md": "- x\n",
            "e.security.md": "- x\n",
            "site-id.note.nl.md": "## Kop\n\nTekst.\n",
            "site-id.note.en.md": "## Heading\n\nText.\n",
        }
        assert fragments.fragment_problems(files) == []
        assert fragments.release_fragment_problems(files) == []

    def test_the_fragments_of_this_repository_are_valid(self):
        repository = git.Git(ROOT)
        files, modes = repository.tree("HEAD", fragments.FRAGMENTS), repository.modes("HEAD", fragments.FRAGMENTS)
        assert fragments.fragment_problems(files, modes) == []

    @pytest.mark.parametrize(
        ("name", "mode"),
        [("x.added.md", "120000"), ("x.added.md", "100755"), ("x.added.md", "160000"), ("README.md", "100755")],
        ids=["symlink", "executable", "submodule", "readme-executable"],
    )
    def test_anything_but_a_plain_file_is_refused(self, name, mode):
        """A symlink's text is its target, so nothing else is said about it."""
        files = {name: "../../etc/passwd", "y.fixed.md": "- y\n"}
        assert fragments.fragment_problems(files, {name: mode, "y.fixed.md": "100644"}) == [
            f"changelog.d/{name} is not a plain file."
        ]

    @pytest.mark.parametrize(
        "name",
        [
            "Site-Id.added.md",
            "site_id.added.md",
            "-site.added.md",
            "site-.added.md",
            "site.improved.md",
            "site.note.md",
            "site.note.de.md",
            "site.added.txt",
            "site.added",
            "notes.md",
            ".gitkeep",
            "sub/site.added.md",
            "sub/README.md",
        ],
    )
    def test_a_name_in_neither_form_is_refused(self, name):
        assert fragments.fragment_problems({name: "- x\n"}) == [f"changelog.d/{name} {self.FORMS}"]

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("### Added\n\n- x\n", "'### Added' is not an entry: every change is a list item starting with '- '."),
            ("- x\n\nSome prose.\n", "'Some prose.' is not an entry"),
            ("  indented, no entry above\n- x\n", "'  indented, no entry above' is not an entry"),
            ("<!-- a comment -->\n- x\n", "'<!-- a comment -->' is not an entry"),
            ("- x\n#### Deeper\n", "'#### Deeper' is not an entry"),
        ],
    )
    def test_anything_but_list_items_is_refused(self, text, reason):
        [problem] = fragments.fragment_problems({"x.fixed.md": text})
        assert problem.startswith("changelog.d/x.fixed.md: ")
        assert reason in problem

    @pytest.mark.parametrize("text", ["", "  \n\n"])
    def test_an_entry_fragment_without_an_entry_is_refused(self, text):
        assert fragments.fragment_problems({"x.fixed.md": text}) == ["changelog.d/x.fixed.md has no entries."]

    @pytest.mark.parametrize(
        ("files", "problem"),
        [
            ({"x.note.nl.md": "Nieuw"}, "x.note.nl.md has no x.note.en.md: " + BOTH_LANGUAGES),
            ({"x.note.en.md": "New"}, "x.note.en.md has no x.note.nl.md: " + BOTH_LANGUAGES),
            ({"x.note.nl.md": " \n", "x.note.en.md": "New"}, "x.note.nl.md is empty."),
        ],
        ids=["no-english", "no-dutch", "empty"],
    )
    def test_a_note_comes_in_both_languages_with_content(self, files, problem):
        assert fragments.fragment_problems(files) == [f"changelog.d/{problem}"]

    @pytest.mark.parametrize(
        "files",
        [{}, {"README.md": "x"}, {"x.note.nl.md": "a", "x.note.en.md": "b"}],
        ids=["nothing", "readme", "notes-only"],
    )
    def test_without_entries_there_is_nothing_to_release(self, files):
        assert fragments.release_fragment_problems(files) == [
            "Nothing to release: changelog.d/ has no entries, and they are the release notes."
        ]

    def test_a_malformed_fragment_is_the_problem_not_the_emptiness(self):
        assert fragments.release_fragment_problems({"bad.md": "- x"}) == [f"changelog.d/bad.md {self.FORMS}"]

    def test_the_paths_leave_the_readme_out(self):
        assert fragments.fragment_paths({"b.added.md": "", "README.md": "", "a.note.nl.md": ""}) == [
            "changelog.d/a.note.nl.md",
            "changelog.d/b.added.md",
        ]

    def test_the_section_follows_the_headings_then_the_file_names(self):
        files = {
            "README.md": "ignored\n",
            "b.added.md": "- B.\n",
            "a.added.md": "- A,\n  continued.\n\n\n- A2.\n",
            "x.security.md": "- X.\n",
            "m.fixed.md": "- M.\n",
            "a.note.nl.md": "ignored here\n",
            "a.note.en.md": "ignored here\n",
        }
        assert fragments.assemble_section(files) == (
            "### Added\n\n- A,\n  continued.\n- A2.\n- B.\n\n### Fixed\n\n- M.\n\n### Security\n\n- X."
        )

    def test_the_notes_are_joined_by_name_with_a_blank_line_between(self):
        files = {
            "b.note.nl.md": "## B\n\nTekst.\n",
            "a.note.nl.md": "\n## A\n\nTekst A.",
            "a.note.en.md": "## A\n\nText A.\n\n",
            "b.note.en.md": "## B\n\nText.\n",
            "a.added.md": "- x\n",
        }
        assert fragments.assemble_notes(files) == {
            "nl": "## A\n\nTekst A.\n\n## B\n\nTekst.\n",
            "en": "## A\n\nText A.\n\n## B\n\nText.\n",
        }
        assert fragments.assemble_notes({"a.added.md": "- x\n"}) == {}
