"""CHANGELOG.md: its sections, the CalVer tags they stand for and the link
references at the bottom."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from toon_release.errors import ReleaseError

CHANGELOG = "CHANGELOG.md"
HEADINGS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")

TAG_RE = re.compile(r"^v[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(\.[0-9]+)?$")
RELEASED_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]$")
UNRELEASED_TITLE = "Unreleased"
UNRELEASED_HEADING = f"## [{UNRELEASED_TITLE}]"
#: The one line under [Unreleased]: the changes themselves wait as fragments.
UNRELEASED_POINTER = "Merged but not released yet: the fragments in [changelog.d/](changelog.d/README.md)."
# The form before releases wrote headings themselves: `## [v2026.9.1] - 2026-09-01`.
TAGGED_HEADING = re.compile(r"^## \[v[^\]]*\]")
SECTION_TITLE = re.compile(r"^## \[([^\]]+)\]")
LINK_REFERENCE = re.compile(r"^\[[^\]]+\]: \S+$")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


REPOSITORY = "DigiGilde/plak"


@dataclass(frozen=True)
class Section:
    title: str
    heading: str
    body: str
    start: int
    end: int


def _trim(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


def split_sections(text: str) -> list[Section]:
    """Every `## ` section, without judging it. The title is what sits
    between the brackets, so the version."""
    lines = text.splitlines()
    bottom = _links_start(lines)
    starts = [i for i, line in enumerate(lines[:bottom]) if line.startswith("## ")]
    sections = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else bottom
        heading = lines[start].rstrip()
        match = SECTION_TITLE.match(heading)
        title = match.group(1) if match else heading[3:].strip()
        sections.append(Section(title, heading, _trim(lines[start + 1 : end]), start, end))
    return sections


def tag_error(tag: str) -> str | None:
    if not TAG_RE.match(tag):
        return f"'{tag}' is not a CalVer tag: vYYYY.M.D, or vYYYY.M.D.N for another release that day."
    parts = tag[1:].split(".")
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        return f"'{tag}' has a leading zero; write v2026.1.5, not v2026.01.05."
    if len(parts) == 4 and parts[3] == "0":
        return f"'{tag}' counts from .1: the first release of a day has no suffix."
    try:
        date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return f"'{tag}' does not name a real date."
    return None


def tag_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag[1:].split("."))


def tag_date(tag: str) -> date:
    return date(*tag_key(tag)[:3])


def _unreleased_problems(sections: list[Section]) -> list[str]:
    """[Unreleased] comes first and holds the pointer to changelog.d/ and
    nothing else: the changes themselves wait there, as fragments."""
    if not sections or sections[0].heading != UNRELEASED_HEADING:
        return [f"{CHANGELOG}: the first section has to be '{UNRELEASED_HEADING}', with under it: {UNRELEASED_POINTER}"]
    body = sections[0].body
    if body == UNRELEASED_POINTER:
        return []
    if any(line.startswith("- ") for line in body.splitlines()):
        return [
            f"{CHANGELOG}: an entry under [Unreleased] belongs in a fragment under changelog.d/ (docs/releasing.md)."
        ]
    return [f"{CHANGELOG}: under [Unreleased] stands one line and nothing else: {UNRELEASED_POINTER}"]


def changelog_problems(text: str) -> list[str]:
    sections = split_sections(text)
    problems = _unreleased_problems(sections)
    seen: set[str] = set()
    for section in sections:
        match = RELEASED_HEADING.match(section.heading)
        if section.title in seen:
            problems.append(f"{CHANGELOG}: '{section.heading}' appears twice.")
        elif section.title == UNRELEASED_TITLE:
            pass  # judged by _unreleased_problems, wherever it stands
        elif TAGGED_HEADING.match(section.heading):
            problems.append(
                f"{CHANGELOG}: '{section.heading}' is the old form; write '## [{section.title[1:]}]', "
                "the version without the v and without a date."
            )
        elif not match:
            problems.append(f"{CHANGELOG}: '{section.heading}' is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'.")
        elif error := tag_error("v" + match["version"]):
            problems.append(f"{CHANGELOG}: '{section.heading}': {error}")
        seen.add(section.title)
    if not problems and text != with_links(text):
        problems.append(
            f"{CHANGELOG}: the link references at the bottom are not the ones its headings need; "
            "`release.py links` writes them."
        )
    return problems


def parse_changelog(text: str) -> list[Section]:
    problems = changelog_problems(text)
    if problems:
        raise ReleaseError(*problems)
    return split_sections(text)


def version_sections(sections: list[Section]) -> list[Section]:
    """The sections that are releases: everything but [Unreleased]."""
    return [section for section in sections if section.title != UNRELEASED_TITLE]


def pending_version(sections: list[Section], tags: Iterable[str]) -> str | None:
    """The newest version section's version while its tag does not exist: a
    release that is still a pull request, or was merged and not tagged yet."""
    versions = version_sections(sections)
    if not versions:
        return None
    version = versions[0].title
    return None if f"v{version}" in set(tags) else version


def release_text(text: str, version: str, body: str) -> str:
    """The changelog with the section for `version`, holding `body`, below
    [Unreleased] and above the newest version. The heading is the version,
    without the v: the tag already is the date."""
    lines = text.splitlines()
    sections = split_sections(text)
    unreleased = next((s for s in sections if s.title == UNRELEASED_TITLE), None)
    at = unreleased.end if unreleased else _links_start(lines)
    before = lines[:at]
    if before and before[-1].strip():
        before.append("")
    section = [f"## [{version}]", "", *body.splitlines(), ""]
    return with_links("\n".join([*before, *section, *lines[at:]]))


def _links_start(lines: list[str]) -> int:
    """Where the link references at the bottom begin, blank lines included;
    len(lines) when there are none."""
    start = len(lines)
    while start and (not lines[start - 1].strip() or LINK_REFERENCE.match(lines[start - 1])):
        start -= 1
    return start


def changelog_links(versions: list[str]) -> list[str]:
    """The link references that make the headings links, newest first: what
    [Unreleased] compares with, a compare view with the previous version
    for each version, the tag for the first."""
    if not versions:
        return []
    base = f"https://github.com/{REPOSITORY}"
    links = [f"[{UNRELEASED_TITLE}]: {base}/compare/v{versions[0]}...HEAD"]
    links += [f"[{newer}]: {base}/compare/v{older}...v{newer}" for newer, older in pairwise(versions)]
    return [*links, f"[{versions[-1]}]: {base}/releases/tag/v{versions[-1]}"]


def with_links(text: str) -> str:
    """The changelog with its link references written anew from its
    headings."""
    lines = text.splitlines()
    body = "\n".join(lines[: _links_start(lines)]).rstrip("\n")
    links = changelog_links([section.title for section in version_sections(split_sections(text))])
    return body + "\n" + ("\n" + "\n".join(links) + "\n" if links else "")


def newest_tag(tags: Iterable[str]) -> str | None:
    calver = [tag for tag in tags if tag_error(tag) is None]
    return max(calver, key=tag_key) if calver else None


def next_tag(day: date, tags: Iterable[str]) -> str:
    existing = set(tags)
    base = f"v{day.year}.{day.month}.{day.day}"
    tag, n = base, 1
    while tag in existing:
        tag, n = f"{base}.{n}", n + 1
    newest = newest_tag(existing)
    if newest and tag_key(tag) <= tag_key(newest):
        raise ReleaseError(f"The next tag {tag} is not newer than {newest}; is the date right?")
    return tag


def today() -> date:
    return datetime.now(AMSTERDAM).date()
