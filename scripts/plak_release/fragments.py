"""The fragments under changelog.d/: one file per change, assembled into a
CHANGELOG section and the What's new notes by a release."""

from __future__ import annotations

import re

from plak_release.changelog import HEADINGS

#: One file per change, turned into the CHANGELOG section by a release.
FRAGMENTS = "changelog.d/"
FRAGMENTS_README = "README.md"
KINDS = {heading.lower(): heading for heading in HEADINGS}
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
ENTRY_FRAGMENT = re.compile(rf"^(?P<slug>{SLUG})\.(?P<kind>{'|'.join(KINDS)})\.md$")
NOTE_FRAGMENT = re.compile(rf"^(?P<slug>{SLUG})\.note\.(?P<lang>nl|en)\.md$")
FRAGMENT_FORMS = f"<slug>.<kind>.md with kind one of {', '.join(KINDS)}, or <slug>.note.nl.md and <slug>.note.en.md"
PLAIN_FILE = "100644"
NOTHING_TO_RELEASE = f"Nothing to release: {FRAGMENTS} has no entries, and they are the release notes."


def _item_problems(name: str, text: str) -> list[str]:
    """An entry fragment holds list items and their continuation lines,
    nothing else, and at least one item."""
    problems = []
    items = 0
    in_item = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("- "):
            items += 1
            in_item = True
            continue
        if in_item and line[0] in " \t":
            continue
        problems.append(
            f"{FRAGMENTS}{name}: '{line}' is not an entry: every change is a list item starting with '- '."
        )
    if not items:
        problems.append(f"{FRAGMENTS}{name} has no entries.")
    return problems


def fragment_problems(files: dict[str, str], modes: dict[str, str] | None = None) -> list[str]:
    """Everything under changelog.d/ is an entry fragment, a What's new note
    in both languages, or the README, each a plain file. `files` maps a name
    under FRAGMENTS to its text, `modes` to its git mode."""
    problems = []
    notes: dict[str, set[str]] = {}
    for name, text in sorted(files.items()):
        if (modes or {}).get(name, PLAIN_FILE) != PLAIN_FILE:
            problems.append(f"{FRAGMENTS}{name} is not a plain file.")
        elif name == FRAGMENTS_README:
            continue
        elif ENTRY_FRAGMENT.match(name):
            problems.extend(_item_problems(name, text))
        elif match := NOTE_FRAGMENT.match(name):
            notes.setdefault(match["slug"], set()).add(match["lang"])
            if not text.strip():
                problems.append(f"{FRAGMENTS}{name} is empty.")
        else:
            problems.append(f"{FRAGMENTS}{name} is not {FRAGMENT_FORMS}.")
    for slug, languages in sorted(notes.items()):
        if len(languages) == 1:
            [lang] = languages
            other = "en" if lang == "nl" else "nl"
            problems.append(
                f"{FRAGMENTS}{slug}.note.{lang}.md has no {slug}.note.{other}.md: "
                "a What's new note comes in Dutch and English."
            )
    return problems


def release_fragment_problems(files: dict[str, str], modes: dict[str, str] | None = None) -> list[str]:
    """What keeps the fragments from becoming a release: a malformed one, or
    no entries at all. Notes alone are no release."""
    problems = fragment_problems(files, modes)
    if not problems and not any(ENTRY_FRAGMENT.match(name) for name in files):
        problems.append(NOTHING_TO_RELEASE)
    return problems


def fragment_paths(files: dict[str, str]) -> list[str]:
    """Every fragment as a path from the repository root, the README aside."""
    return [FRAGMENTS + name for name in sorted(files) if name != FRAGMENTS_README]


def assemble_section(files: dict[str, str]) -> str:
    """The section body the fragments make: a `### ` block per heading that
    has entries, in HEADINGS order; fragments by name, items in file order."""
    blocks = []
    for kind, heading in KINDS.items():
        items = [
            line
            for name in sorted(files)
            if (match := ENTRY_FRAGMENT.match(name)) and match["kind"] == kind
            for line in files[name].splitlines()
            if line.strip()
        ]
        if items:
            blocks.append("\n".join([f"### {heading}", "", *items]))
    return "\n\n".join(blocks)


def assemble_notes(files: dict[str, str]) -> dict[str, str]:
    """The What's new note per language, the note fragments by name with a
    blank line between them; empty without note fragments."""
    parts: dict[str, list[str]] = {}
    for name in sorted(files):
        if match := NOTE_FRAGMENT.match(name):
            parts.setdefault(match["lang"], []).append(files[name].strip())
    return {lang: "\n\n".join(texts) + "\n" for lang, texts in parts.items()}
