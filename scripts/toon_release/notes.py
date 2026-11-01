"""The What's new notes under frontend/src/content/releases/."""

from __future__ import annotations

import re

NOTES_DIR = "frontend/src/content/releases/"
NOTE_NAME = re.compile(r"^(?P<key>[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]+)?)\.(?P<lang>nl|en)\.md$")


def note_problems(notes: dict[str, str]) -> list[str]:
    """Every note is named after its release and comes in Dutch and English,
    both with content or both without. `notes` maps a name under NOTES_DIR
    to its text."""
    pairs: dict[str, dict[str, str]] = {}
    problems = []
    for name, content in sorted(notes.items()):
        match = NOTE_NAME.match(name)
        if match:
            pairs.setdefault(match["key"], {})[match["lang"]] = content
        else:
            problems.append(f"{NOTES_DIR}{name} is not <version>.nl.md or <version>.en.md, the note of a release.")
    for key, languages in sorted(pairs.items()):
        if len(languages) == 1:
            [(lang, _)] = languages.items()
            other = "en" if lang == "nl" else "nl"
            problems.append(
                f"{NOTES_DIR}{key}.{lang}.md has no {key}.{other}.md: a What's new note comes in Dutch and English."
            )
        elif bool(languages["nl"].strip()) != bool(languages["en"].strip()):
            problems.append(f"{NOTES_DIR}{key}.nl.md and {key}.en.md: one is empty and the other is not.")
    return problems
