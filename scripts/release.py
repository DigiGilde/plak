# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Releases from CHANGELOG.md: CalVer tags, component versions, the checks.

    prepare             turn the fragments in changelog.d/ into the next release, staged
    verify-staged --tag T   exit 1 unless the index holds only that release
    decide              what a push to beta does: tag the newest section, or nothing
    pending             print version= of the newest section while it waits for its tag
    refresh-tag --branch B  print tag= for refreshing the release pull request on branch B
    release-pr --repo R     read `gh pr list --json` on stdin, print number=, old_tag=, old_sha=
    notes --tag T       the GitHub Release body for T
    validate-tag T      exit 1 unless T is a CalVer tag
    check --base REF    the pull request check on CHANGELOG.md, changelog.d/ and the notes
    api-check           the pull request check on the API contract
    hook                the Claude Code PreToolUse hook for `gh pr create`
    links               write the link references at the bottom of CHANGELOG.md

Run from anywhere inside the repository. docs/releasing.md has the model.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from toon_release.api import oasdiff
from toon_release.changelog import CHANGELOG, tag_error, today, with_links
from toon_release.commands import (
    _append,
    _read,
    _read_spec,
    api_check,
    check,
    decide,
    hook,
    pending_release,
    prepare,
    refresh_tag,
    release_notes,
    release_pull_request,
    report_api_check,
    report_check,
    verify_staged,
)
from toon_release.errors import ReleaseError
from toon_release.git import Git


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Releases from CHANGELOG.md (docs/releasing.md)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("decide", help="print route=release|none, tag= and commit=, also to $GITHUB_OUTPUT")
    preparing = commands.add_parser("prepare", help="stage the release the fragments in changelog.d/ make")
    preparing.add_argument("--tag", help="the tag to release under, instead of today's")
    commands.add_parser("pending", help="print version= of a release that waits for its tag, also to $GITHUB_OUTPUT")
    refreshing = commands.add_parser("refresh-tag", help="print tag= for the refreshed release pull request")
    refreshing.add_argument("--branch", required=True, help="the head branch of the open release pull request")
    listing = commands.add_parser("release-pr", help="the open release pull request in `gh pr list --json` on stdin")
    listing.add_argument("--repo", required=True, help="OWNER/NAME; a fork's release branch does not count")
    commands.add_parser("verify-staged", help="exit 1 unless the index holds only the release for TAG").add_argument(
        "--tag", required=True
    )
    noting = commands.add_parser("notes", help="release notes")
    noting.add_argument("--tag", required=True)
    noting.add_argument("--image", help="the image name, for the container section")
    noting.add_argument("--digest", help="the image digest, for the container section")
    commands.add_parser("validate-tag", help="exit 1 unless TAG is a CalVer tag").add_argument("tag")
    checking = commands.add_parser("check", help="the pull request check")
    checking.add_argument("--base", required=True, help="the ref the pull request goes into")
    checking.add_argument("--body-file", type=Path, help="the pull request description")
    checking.add_argument("--comment-file", type=Path, help="write the sticky comment here, empty for none")
    checking.add_argument("--hard-only", action="store_true", help="only what fails the check")
    api_checking = commands.add_parser("api-check", help="the pull request check on the API contract")
    api_checking.add_argument("--comment-file", type=Path, help="write the sticky comment here, empty for none")
    for command in (preparing, api_checking):
        command.add_argument(
            "--spec", type=Path, required=True, help="the schema `python -m toon.api.openapi_file` prints"
        )
        command.add_argument("--oasdiff", required=True, help="the oasdiff binary")
    commands.add_parser("hook", help="Claude Code PreToolUse hook, reads the event on stdin")
    commands.add_parser("links", help="write the link references at the bottom of CHANGELOG.md")
    args = parser.parse_args(argv)

    if args.command == "hook":
        decision = hook(sys.stdin.read())
        if decision:
            print(decision)
        return 0
    if args.command == "validate-tag":
        error = tag_error(args.tag)
        print(error or f"{args.tag} is a valid CalVer tag.", file=sys.stderr if error else sys.stdout)
        return 1 if error else 0

    try:
        if args.command == "release-pr":
            number, tag, sha = release_pull_request(sys.stdin.read(), args.repo)
            output = f"number={number}\nold_tag={tag}\nold_sha={sha}\n"
            print(output, end="")
            _append("GITHUB_OUTPUT", output)
            return 0
        git = Git(Path.cwd())
        if args.command == "decide":
            route, tag, commit = decide(git)
            output = f"route={route}\ntag={tag}\ncommit={commit}\n"
            print(output, end="")
            _append("GITHUB_OUTPUT", output)
            return 0
        if args.command == "pending":
            output = f"version={pending_release(git)}\n"
            print(output, end="")
            _append("GITHUB_OUTPUT", output)
            return 0
        if args.command == "refresh-tag":
            output = f"tag={refresh_tag(git, args.branch)}\n"
            print(output, end="")
            _append("GITHUB_OUTPUT", output)
            return 0
        if args.command == "prepare":
            tag, touched = prepare(git, today(), _read_spec(args.spec), oasdiff(args.oasdiff), args.tag)
            print(f"tag={tag}")
            for path in touched:
                print(f"updated {path}")
            _append("GITHUB_OUTPUT", f"tag={tag}\n")
            return 0
        if args.command == "notes":
            print(release_notes(git, args.tag, args.image, args.digest), end="")
            return 0
        if args.command == "links":
            path = git.root / CHANGELOG
            path.write_text(with_links(_read(git, CHANGELOG)), encoding="utf-8")
            print(f"updated {CHANGELOG}")
            return 0
        if args.command == "verify-staged":
            if problems := verify_staged(git, args.tag):
                raise ReleaseError(*problems)
            print(f"The index holds the release for {args.tag} and nothing else.")
            return 0
        if args.command == "api-check":
            result = api_check(git, _read_spec(args.spec), oasdiff(args.oasdiff))
            return report_api_check(result, args.comment_file)
        body = args.body_file.read_text(encoding="utf-8") if args.body_file else ""
        return report_check(check(git, args.base, body, args.hard_only), args.comment_file)
    except ReleaseError as error:
        for problem in error.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - the script entry; main() is tested directly
    raise SystemExit(main())
