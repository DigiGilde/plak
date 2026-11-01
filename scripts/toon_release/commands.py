"""The subcommands of scripts/release.py and the reports they print."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from toon_release.api import (
    API_VERSION_FILE,
    API_VERSION_VALUE,
    OPENAPI,
    Diff,
    _where,
    api_version,
    next_api_version,
    parse_spec,
    render_spec,
    set_api_version,
)
from toon_release.changelog import (
    CHANGELOG,
    REPOSITORY,
    changelog_problems,
    newest_tag,
    next_tag,
    parse_changelog,
    pending_version,
    release_text,
    split_sections,
    tag_date,
    tag_error,
    tag_key,
    today,
    version_sections,
)
from toon_release.errors import ReleaseError
from toon_release.fragments import (
    ENTRY_FRAGMENT,
    FRAGMENTS,
    NOTE_FRAGMENT,
    PLAIN_FILE,
    assemble_notes,
    assemble_section,
    fragment_paths,
    fragment_problems,
    release_fragment_problems,
)
from toon_release.git import Git
from toon_release.notes import NOTES_DIR, note_problems
from toon_release.versions import (
    ALWAYS_WRITTEN,
    BACKEND_LOCK,
    BACKEND_PYPROJECT,
    CLI_LOCK,
    CLI_PATHS,
    CLI_PYPROJECT,
    FRONTEND_LOCK,
    FRONTEND_PACKAGE,
    MEMBER_FACING,
    PLUGIN_MANIFEST,
    PLUGIN_PATHS,
    PUBLICCODE,
    RELEASE_PATHS,
    VERSION_OF,
    _under,
    component_changed,
    is_shipped,
    pyproject_version,
    set_lock_version,
    set_package_version,
    set_plugin_version,
    set_publiccode,
    set_pyproject_version,
)

BASE_BRANCH = "origin/beta"


COMMENT_MARKER = "<!-- toon-changelog-check -->"
API_COMMENT_MARKER = "<!-- toon-api-check -->"
OPT_OUT = re.compile(r"""(?:^|["'])[ \t]*No changelog entry:[ \t]*\S""", re.MULTILINE)
GH_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")


def release_problems(git: Git, ref: str, version: str) -> list[str]:
    """What has to hold at `ref` for it to be the release of `version`: a
    tag that is valid and newer than every other, no fragment left, and
    the version in the files a release writes it into."""
    tag = f"v{version}"
    problems = []
    if error := tag_error(tag):
        problems.append(error)
    elif (newest := newest_tag(git.tags())) and tag_key(tag) <= tag_key(newest):
        problems.append(f"{tag} is not newer than {newest}.")
    left = fragment_paths(git.tree(ref, FRAGMENTS))
    problems.extend(f"{path} is still there: a release removes every fragment." for path in left)
    for path, version_of in VERSION_OF.items():
        text = git.show(ref, path)
        try:
            found = version_of(text) if text is not None else None
        except (ReleaseError, ValueError):
            found = None
        if found != version:
            problems.append(f"{path} at {ref} does not carry version {version}.")
    return problems


def _is_release_commit(git: Git, commit: str) -> bool:
    """Whether `commit` touches nothing but what a release writes: its files,
    the notes, and fragments it removes."""
    for status, path in git.changes(commit):
        if path in RELEASE_PATHS or path.startswith(NOTES_DIR) or (path.startswith(FRAGMENTS) and status == "D"):
            continue
        return False
    return True


def release_head(git: Git, version: str) -> str:
    """The commit to tag for `version`: the one that added its section, and
    every commit after it on the first-parent line of HEAD that only edits
    the release (a reword in the pull request lands as a commit of its
    own), up to the first that changes anything else."""
    head = git.run("log", "-1", "--format=%H", f"-S## [{version}]", "--", CHANGELOG).strip()
    for commit in git.run("rev-list", "--reverse", "--first-parent", f"{head}..HEAD").split():
        if not _is_release_commit(git, commit):
            break
        head = commit
    return head


def decide(git: Git) -> tuple[str, str, str]:
    """('release', tag, commit) when the newest section of CHANGELOG.md has
    no tag yet, with the last commit of that release; ('none', '', '')
    otherwise. The commit is checked before it is named."""
    sections = split_sections(_read(git, CHANGELOG))
    version = pending_version(sections, git.tags())
    if version is None:
        return "none", "", ""
    commit = release_head(git, version)
    if problems := release_problems(git, commit, version):
        raise ReleaseError(*problems)
    return "release", f"v{version}", commit


def pending_release(git: Git) -> str:
    """The version of beta's newest section while it waits for its tag, or
    '': a release pull request is not refreshed over a release on its way."""
    return pending_version(split_sections(_read(git, CHANGELOG)), git.tags()) or ""


PULL_NUMBER = re.compile(r"^[0-9]+$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def release_pull_request(listing: str, repository: str) -> tuple[str, str, str]:
    """The open release pull request in `gh pr list --json
    number,headRefName,headRefOid,headRepositoryOwner` output: (number, tag,
    head sha), or three empty strings. Only a `release/vX` branch of
    `repository` itself counts; a fork's does not. Both jobs of
    prepare-release.yml run this, so neither trusts the other's answer."""
    try:
        pulls = json.loads(listing)
    except ValueError:
        raise ReleaseError("The pull request listing is not JSON.") from None
    if not isinstance(pulls, list):
        raise ReleaseError("The pull request listing is not a list.")
    owner = repository.split("/")[0]
    for pull in pulls:
        branch = str(pull.get("headRefName", ""))
        tag = branch.removeprefix("release/")
        ours = pull.get("headRepositoryOwner", {}).get("login") == owner
        if not branch.startswith("release/") or tag_error(tag) or not ours:
            continue
        number, sha = str(pull.get("number", "")), str(pull.get("headRefOid", ""))
        if not PULL_NUMBER.match(number) or not COMMIT_SHA.match(sha):
            raise ReleaseError(f"Pull request {number!r} on {branch} has no plain number and head sha.")
        return number, tag, sha
    return "", "", ""


def refresh_tag(git: Git, branch: str) -> str:
    """The tag a refreshed release pull request keeps or gets: the one in
    its branch name while verify-staged would still take it (dated today or
    yesterday, newer than every tag), else the next tag."""
    if not branch.startswith("release/"):
        raise ReleaseError(f"'{branch}' is not a release branch: release/vYYYY.M.D.")
    tag = branch.removeprefix("release/")
    tags = git.tags()
    if tag_error(tag) is None and not _tag_problems(tag, tags):
        return tag
    return next_tag(today(), tags)


def prepare(git: Git, day: date, spec: str, diff: Diff, tag: str | None = None) -> tuple[str, list[str]]:
    """Writes the release into the working tree and stages it: the section
    from the fragments at HEAD, the versions, the notes, the fragments
    removed. Returns the tag and the paths it touched. Refuses before it
    writes anything.

    `spec` is the API schema of HEAD, as toon.api.openapi_file prints it;
    `diff` compares it with the one the previous release committed."""
    tags = git.tags()
    if tag is None:
        tag = next_tag(day, tags)
    elif error := tag_error(tag):
        raise ReleaseError(error)
    text = _read(git, CHANGELOG)
    sections = parse_changelog(text)
    fragments = git.tree("HEAD", FRAGMENTS)
    if problems := release_fragment_problems(fragments, git.modes("HEAD", FRAGMENTS)):
        raise ReleaseError(*problems)
    if pending := pending_version(sections, tags):
        raise ReleaseError(f"v{pending} is waiting for its tag: merge or close that release first.")
    if tag in tags:
        raise ReleaseError(f"The tag {tag} already exists.")
    previous = newest_tag(tags)
    if previous and tag_key(tag) <= tag_key(previous):
        raise ReleaseError(f"{tag} is not newer than {previous}.")
    version = tag[1:]
    if any(section.title == version for section in sections):
        raise ReleaseError(f"{CHANGELOG} already has a section for {tag}.")

    changed = git.changed(previous) if previous else []
    writes = {CHANGELOG: release_text(text, version, assemble_section(fragments))}
    if previous is None or component_changed(git, previous, changed, CLI_PATHS):
        writes[CLI_PYPROJECT] = set_pyproject_version(_read(git, CLI_PYPROJECT), version)
        writes[CLI_LOCK] = set_lock_version(_read(git, CLI_LOCK), version)
    if previous is None or component_changed(git, previous, changed, PLUGIN_PATHS):
        writes[PLUGIN_MANIFEST] = set_plugin_version(_read(git, PLUGIN_MANIFEST), version)
    writes[PUBLICCODE] = set_publiccode(_read(git, PUBLICCODE), version, tag_date(tag))
    # Every tag builds a new image, so its packages follow every release.
    writes[BACKEND_PYPROJECT] = set_pyproject_version(_read(git, BACKEND_PYPROJECT), version, BACKEND_PYPROJECT)
    writes[BACKEND_LOCK] = set_lock_version(_read(git, BACKEND_LOCK), version, "toon-api", BACKEND_LOCK)
    writes[FRONTEND_PACKAGE] = set_package_version(_read(git, FRONTEND_PACKAGE), version)
    writes[FRONTEND_LOCK] = set_package_version(_read(git, FRONTEND_LOCK), version, FRONTEND_LOCK)
    head = parse_spec(spec, "The API schema")
    base_text = git.show(previous, OPENAPI) if previous else None
    base = parse_spec(base_text, f"{OPENAPI} at {previous}") if base_text is not None else None
    api = next_api_version(base, head, diff(base_text, spec) if base_text is not None else [])
    writes[OPENAPI] = render_spec(head, api)
    code = _read(git, API_VERSION_FILE)
    if (edited := set_api_version(code, api)) != code:
        writes[API_VERSION_FILE] = edited
    for lang, note in assemble_notes(fragments).items():
        path = f"{NOTES_DIR}{version}.{lang}.md"
        if git.show("HEAD", path) is not None:
            raise ReleaseError(f"{path} already exists.")
        writes[path] = note

    for path, content in writes.items():
        (git.root / path).parent.mkdir(parents=True, exist_ok=True)
        (git.root / path).write_text(content, encoding="utf-8")
    git.run("add", "--", *writes)
    removed = fragment_paths(fragments)
    git.run("rm", "-q", "--", *removed)
    return tag, [*writes, *removed]


def verify_staged(git: Git, tag: str) -> list[str]:
    """What the job that commits checks first: what is staged is what
    prepare writes for `tag` on HEAD, and nothing else. The job that wrote
    it ran the dependencies, so its patch is not trusted."""
    if error := tag_error(tag):
        return [error]
    if problems := _tag_problems(tag, git.tags()):
        return problems
    staged = _staged_modes(git)
    if not staged:
        return ["Nothing is staged to release."]
    fragments = git.tree("HEAD", FRAGMENTS)
    if problems := release_fragment_problems(fragments, git.modes("HEAD", FRAGMENTS)):
        return problems
    version = tag[1:]
    day = tag_date(tag)
    api = _staged_api_version(git, problems)
    writes: dict[str, Callable[[str], str]] = {
        CHANGELOG: lambda text: release_text(text, version, assemble_section(fragments)),
        CLI_PYPROJECT: lambda text: set_pyproject_version(text, version),
        CLI_LOCK: lambda text: set_lock_version(text, version),
        PLUGIN_MANIFEST: lambda text: set_plugin_version(text, version),
        PUBLICCODE: lambda text: set_publiccode(text, version, day),
        BACKEND_PYPROJECT: lambda text: set_pyproject_version(text, version, BACKEND_PYPROJECT),
        BACKEND_LOCK: lambda text: set_lock_version(text, version, "toon-api", BACKEND_LOCK),
        FRONTEND_PACKAGE: lambda text: set_package_version(text, version),
        FRONTEND_LOCK: lambda text: set_package_version(text, version, FRONTEND_LOCK),
        API_VERSION_FILE: lambda text: set_api_version(text, api or ""),
    }
    creates = {f"{NOTES_DIR}{version}.{lang}.md": note for lang, note in assemble_notes(fragments).items()}
    removes = set(fragment_paths(fragments))
    # A new schema version without a staged API_VERSION has to be the one
    # the code already has.
    if api and API_VERSION_FILE not in staged:
        staged[API_VERSION_FILE] = PLAIN_FILE
    for path, mode in staged.items():
        before, after = git.show("HEAD", path), git.show("", path)
        if mode not in (PLAIN_FILE, "000000"):
            problems.append(f"{path} is not staged as a plain file.")
        elif path == OPENAPI:
            continue
        elif path in writes:
            try:
                expected = writes[path](before) if before is not None else None
            except ReleaseError:
                expected = None
            if after is None or after != expected:
                problems.append(f"{path} is not what the release writes for {tag}.")
        elif path in creates:
            if before is not None or after != creates[path]:
                problems.append(f"{path} is not what the release writes for {tag}.")
        elif path in removes:
            if after is not None:
                problems.append(f"{path} is a fragment the release removes.")
        else:
            problems.append(f"{path} is not a file a release writes.")
    problems.extend(f"{path} is a fragment the release removes." for path in sorted(removes - staged.keys()))
    required = (*ALWAYS_WRITTEN, *creates)
    problems.extend(f"{path} is missing from the release." for path in required if path not in staged)
    return problems


def _tag_problems(tag: str, tags: Iterable[str]) -> list[str]:
    """A tag the job that ran the dependencies chose: dated today or
    yesterday, and after every tag there is."""
    problems = []
    day, now = tag_date(tag), today()
    if not (now - timedelta(days=1) <= day <= now):
        problems.append(f"{tag} is not dated today or yesterday ({now.isoformat()}).")
    if (newest := newest_tag(tags)) and tag_key(tag) <= tag_key(newest):
        problems.append(f"{tag} is not newer than {newest}.")
    return problems


def _staged_modes(git: Git) -> dict[str, str]:
    """Every staged path with its new mode; 000000 for a removed one."""
    fields = git.run("diff", "--cached", "--raw", "--no-renames", "-z").split("\0")
    return {path: meta.split()[1] for meta, path in zip(fields[::2], fields[1::2], strict=False) if path}


def _staged_api_version(git: Git, problems: list[str]) -> str | None:
    """The API version the staged schema declares, which API_VERSION has to
    match. The schema itself is data the release does not ship."""
    text = git.show("", OPENAPI)
    if text is None or text == git.show("HEAD", OPENAPI):
        return None
    try:
        return "{}.{}.{}".format(*api_version(parse_spec(text, OPENAPI), OPENAPI))
    except ReleaseError as error:
        problems.extend(error.problems)
        return None


def _read(git: Git, path: str) -> str:
    try:
        return (git.root / path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ReleaseError(f"{path} is missing.") from None


def _read_spec(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise ReleaseError(f"{path}: cannot read the API schema.") from None


def release_notes(git: Git, tag: str, image: str | None = None, digest: str | None = None) -> str:
    """The body of the GitHub Release for `tag`, read from the tag itself.
    With the image's name and digest it ends with how to verify it."""
    if error := tag_error(tag):
        raise ReleaseError(error)
    changelog = git.show(tag, CHANGELOG)
    section = next((s for s in split_sections(changelog or "") if s.title == tag[1:]), None)
    if section is None or not section.body:
        raise ReleaseError(f"No section for {tag} in {CHANGELOG} at {tag}.")
    pyproject, plugin = git.show(tag, CLI_PYPROJECT), git.show(tag, PLUGIN_MANIFEST)
    if pyproject is None or plugin is None:
        raise ReleaseError(f"{CLI_PYPROJECT} or {PLUGIN_MANIFEST} is missing at {tag}.")
    version = tag[1:]
    lines = [section.body]
    for name, current in (("CLI", pyproject_version(pyproject)), ("Plugin", json.loads(plugin)["version"])):
        lines.append(f"{name} {current}" if current == version else f"{name} unchanged ({current})")
    # Tags from before the release step wrote the schema have none.
    if (spec := git.show(tag, OPENAPI)) is not None:
        source = f"{OPENAPI} at {tag}"
        lines.append("API {}.{}.{}".format(*api_version(parse_spec(spec, source), source)))
    if image or digest:
        lines.append(container_section(image or "", digest or "", version))
    return "\n\n".join(lines) + "\n"


IMAGE_NAME = re.compile(r"^[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9._-]+)+$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def container_section(image: str, digest: str, version: str) -> str:
    if not IMAGE_NAME.match(image) or not DIGEST.match(digest):
        raise ReleaseError(f"'{image}' and '{digest}' are not an image name and a sha256 digest.")
    reference = f"{image}@{digest}"
    return (
        "## Container image\n\n"
        f"- plak: `{image}:{version}`, digest `{digest}`\n\n"
        f"Verify where it was built: `gh attestation verify oci://{reference} -R {REPOSITORY}`\n"
        "Its SBOM (CycloneDX): the same command with `--predicate-type https://cyclonedx.org/bom`"
    )


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warning: str | None = None
    hint: str | None = None


WARNING = (
    f"Shipped paths changed, but no fragment was added under {FRAGMENTS}. Add one for this change "
    "(docs/releasing.md), or put a line starting with 'No changelog entry:' and the reason in the "
    "pull request description."
)
HINT = (
    "frontend/src/ or cli/plak_cli/ changed without a What's new note. Is this visible to a member? "
    f"Then add one: {FRAGMENTS}<slug>.note.nl.md and <slug>.note.en.md."
)


def _fragments_touched(git: Git, changed: list[str], pattern: re.Pattern[str]) -> bool:
    """Whether `changed` holds a fragment of `pattern`'s form that HEAD still
    has, so one added or edited rather than removed."""
    return any(
        pattern.match(path[len(FRAGMENTS) :]) and git.show("HEAD", path) is not None
        for path in changed
        if path.startswith(FRAGMENTS)
    )


def added_sections(git: Git, base: str) -> list[str]:
    """The versions HEAD's CHANGELOG.md has and `base`'s does not, newest
    first: any at all makes this a release pull request."""
    before = {s.title for s in split_sections(git.show(base, CHANGELOG) or "")}
    after = version_sections(split_sections(git.show("HEAD", CHANGELOG) or ""))
    return [s.title for s in after if s.title not in before]


def entry_missing(git: Git, base: str, body: str = "") -> bool:
    """The warning condition: shipped paths changed since the merge base with
    `base`, no entry fragment came with them, this is no release pull
    request, and `body` does not opt out."""
    changed = git.changed(git.merge_base(base))
    if not any(map(is_shipped, changed)) or added_sections(git, base):
        return False
    return not _fragments_touched(git, changed, ENTRY_FRAGMENT) and not OPT_OUT.search(body)


def check(git: Git, base: str, body: str = "", hard_only: bool = False) -> CheckResult:
    result = CheckResult()
    head = git.show("HEAD", CHANGELOG)
    if head is None:
        result.errors.append(f"{CHANGELOG} is missing.")
        return result
    result.errors.extend(changelog_problems(head))
    result.errors.extend(fragment_problems(git.tree("HEAD", FRAGMENTS), git.modes("HEAD", FRAGMENTS)))
    result.errors.extend(note_problems(git.tree("HEAD", NOTES_DIR)))

    before = {s.title: s for s in split_sections(git.show(base, CHANGELOG) or "")}
    after = {s.title: s for s in split_sections(head)}
    # Frozen is a section the base already has and whose tag exists. One that
    # only this change adds is new, also when its tag is older than it.
    released = {tag[1:] for tag in git.tags() if tag_error(tag) is None}
    for version in sorted(before.keys() & released, key=lambda v: tag_key("v" + v)):
        old, new = before[version], after.get(version)
        if new is None or (old.heading, old.body) != (new.heading, new.body):
            result.errors.append(
                f"{CHANGELOG}: [{version}] was released as v{version} and is frozen; "
                f"describe the change in a fragment under {FRAGMENTS} instead."
            )
    # A pull request that adds a section is a release: one section, on top,
    # and the commit has to be releasable as it is.
    added = added_sections(git, base)
    newest = [section.title for section in version_sections(split_sections(head))[:1]]
    if added and added != newest:
        result.errors.append(
            f"{CHANGELOG}: a release adds one section, on top; this adds {', '.join(f'[{t}]' for t in added)}."
        )
    elif added and tag_error(f"v{added[0]}") is None:
        result.errors.extend(release_problems(git, "HEAD", added[0]))
    if hard_only:
        return result

    if entry_missing(git, base, body):
        result.warning = WARNING
    changed = git.changed(git.merge_base(base))
    visible = [p for p in changed if is_shipped(p) and _under(p, MEMBER_FACING) and not p.startswith(NOTES_DIR)]
    if visible and not _fragments_touched(git, changed, NOTE_FRAGMENT):
        result.hint = HINT
    return result


def _annotation(kind: str, title: str, message: str) -> str:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{kind} title={title}::{escaped}"


def _append(variable: str, text: str) -> None:
    path = os.environ.get(variable)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def report_check(result: CheckResult, comment_file: Path | None) -> int:
    for error in result.errors:
        print(_annotation("error", "Changelog", error))
    if result.warning:
        print(_annotation("warning", "Changelog", result.warning))
    if result.hint:
        print(_annotation("notice", "What's new", result.hint))
    if not (result.errors or result.warning or result.hint):
        print(f"{CHANGELOG}, {FRAGMENTS} and the What's new notes are in order.")

    summary = [f"- **Error:** {e}" for e in result.errors]
    summary += [f"- **Warning:** {result.warning}"] if result.warning else []
    summary += [f"- **Hint:** {result.hint}"] if result.hint else []
    if summary:
        _append("GITHUB_STEP_SUMMARY", "## Changelog\n\n" + "\n".join(summary) + "\n")
    if comment_file is not None:
        comment = f"{COMMENT_MARKER}\n**Changelog:** {result.warning}\n" if result.warning else ""
        comment_file.write_text(comment, encoding="utf-8")
    return 1 if result.errors else 0


@dataclass
class ApiCheckResult:
    errors: list[str] = field(default_factory=list)
    notice: str | None = None
    comment: str = ""


STALE_SCHEMA = (
    f"{OPENAPI} is not the schema this release ships: the API changed after the release was prepared; "
    "prepare it again."
)


def _change_line(change: dict[str, Any]) -> str:
    return f"- `{_where(change)}`: {change.get('text')}"


def _ships_api(git: Git, head: dict[str, Any], version: str) -> bool:
    """Whether HEAD commits the schema of its own code at `version`, in
    backend/openapi.json and in API_VERSION."""
    match = API_VERSION_VALUE.search(git.show("HEAD", API_VERSION_FILE) or "")
    return bool(match) and match.group(1) == version and git.show("HEAD", OPENAPI) == render_spec(head, version)


def api_check(git: Git, spec: str, diff: Diff) -> ApiCheckResult:
    """A pull request against the contract the newest release committed:
    a breaking change fails unless the major went up. Every change the
    schema shows goes into the comment, with the version it leads to. A
    release that is not tagged yet has to ship the schema of its own code."""
    result = ApiCheckResult()
    head = parse_spec(spec, "The API schema")
    tags = git.tags()
    tag = newest_tag(tags)
    base_text = git.show(tag, OPENAPI) if tag else None
    if base_text is None:
        result.notice = f"No {OPENAPI} at {tag or 'any tag'} yet: the next release writes it, and checks start there."
        return result
    base = parse_spec(base_text, f"{OPENAPI} at {tag}")
    changes = diff(base_text, spec)
    try:
        version = next_api_version(base, head, changes)
    except ReleaseError as error:
        result.errors.extend(error.problems)
        version = None
    if changes:
        outcome = f"The next release makes it API {version}." if version else "This cannot be released as it is."
        result.comment = (
            f"{API_COMMENT_MARKER}\n**API:** changes against the contract of {tag}.\n\n"
            + "\n".join(map(_change_line, changes))
            + f"\n\n{outcome}\n"
        )
    # Only the release itself ships its schema: an API change queued behind
    # it, or checked between its merge and its tag, is the next release's.
    pending = pending_version(split_sections(git.show("HEAD", CHANGELOG) or ""), tags)
    if version and pending and release_head(git, pending) == git.head() and not _ships_api(git, head, version):
        result.errors.append(STALE_SCHEMA)
    return result


def report_api_check(result: ApiCheckResult, comment_file: Path | None) -> int:
    for error in result.errors:
        print(_annotation("error", "API contract", error))
    if result.notice:
        print(_annotation("notice", "API contract", result.notice))
    if not (result.errors or result.notice):
        print("The API contract holds.")
    if result.errors:
        _append("GITHUB_STEP_SUMMARY", "## API contract\n\n" + "\n".join(f"- {e}" for e in result.errors) + "\n")
    if comment_file is not None:
        comment_file.write_text(result.comment, encoding="utf-8")
    return 1 if result.errors else 0


def _body_file(command: str, cwd: Path) -> str:
    try:
        words = shlex.split(command)
    except ValueError:
        return ""
    for n, word in enumerate(words):
        if word.startswith("--body-file="):
            name = word.split("=", 1)[1]
        elif word in ("--body-file", "-F") and n + 1 < len(words):
            name = words[n + 1]
        else:
            continue
        try:
            return (cwd / name).read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def hook(stdin: str) -> str | None:
    """The PreToolUse decision for a Bash call: a deny as JSON, or None to
    let it through. Anything unexpected lets it through; CI still checks."""
    try:
        event = json.loads(stdin)
        command = str(event["tool_input"]["command"])
        cwd = Path(event.get("cwd") or ".")
    except (ValueError, KeyError, TypeError):
        return None
    if not GH_PR_CREATE.search(command) or OPT_OUT.search(command):
        return None
    try:
        missing = entry_missing(Git(cwd), BASE_BRANCH, _body_file(command, cwd))
    except ReleaseError as error:
        print(f"changelog hook: skipped, {error}", file=sys.stderr)
        return None
    if not missing:
        return None
    reason = (
        f"Shipped paths changed without a changelog fragment. Write {FRAGMENTS}<slug>.<kind>.md (see the "
        "changelog skill and docs/releasing.md), or add a line 'No changelog entry: <reason>' to the "
        "pull request body."
    )
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
