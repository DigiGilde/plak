"""The What's new notes (plak_release.notes)."""

from __future__ import annotations

import pytest

from plak_release.notes import NOTES_DIR, note_problems


class TestNotes:
    def test_pairs_in_both_languages_are_fine(self):
        notes = {"2026.9.1.nl.md": "a", "2026.9.1.en.md": "b", "2026.10.4.1.nl.md": "", "2026.10.4.1.en.md": " \n"}
        assert note_problems(notes) == []

    @pytest.mark.parametrize(
        ("notes", "reason"),
        [
            ({"2026.9.1.nl.md": "Nieuw"}, "2026.9.1.nl.md has no 2026.9.1.en.md"),
            ({"2026.9.1.en.md": "New"}, "2026.9.1.en.md has no 2026.9.1.nl.md"),
            ({"2026.9.1.nl.md": "Nieuw", "2026.9.1.en.md": "\n"}, "one is empty and the other is not"),
        ],
    )
    def test_a_note_in_one_language_is_refused(self, notes, reason):
        [problem] = note_problems(notes)
        assert problem.startswith(NOTES_DIR)
        assert reason in problem

    @pytest.mark.parametrize(
        "name", ["unreleased.nl.md", "index.ts", "README.md", "v2026.9.1.nl.md", "2026.9.1.de.md", "sub/2026.9.1.nl.md"]
    )
    def test_anything_not_named_after_a_release_is_refused(self, name):
        """The same rule the frontend test holds the directory to."""
        assert note_problems({name: "x", "2026.9.1.nl.md": "a", "2026.9.1.en.md": "b"}) == [
            f"frontend/src/content/releases/{name} is not <version>.nl.md or <version>.en.md, the note of a release."
        ]
