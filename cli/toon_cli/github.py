"""Reports a Toon deploy back to GitHub for the publish action: a deployment
in an environment, and a comment on the pull request with the link.

Not a `toon` subcommand: actions/publish runs it as `python -P -m
toon_cli.github`, with the runner's GITHUB_* variables in the environment.

Usage:
    python -m toon_cli.github publish --site <group/site> --url <url> \
        --version-id <id> [--access <access>] [--preview-ref <ref>] \
        [--environment <name>] [--comment]
    python -m toon_cli.github teardown --site <group/site> --preview-ref <ref> \
        [--environment <name>] [--comment]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from functools import cached_property

import httpx

from toon_cli import ACCESS_BASE_WHO, VERSION

PER_PAGE = 100


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, client: httpx.Client, repository: str) -> None:
        self.client = client
        self.repository = repository

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"/repos/{self.repository}{path}"
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise GitHubError(f"could not reach the GitHub API: {error}") from None
        if response.is_success:
            return response
        raise GitHubError(
            f"{method} {url} answered {response.status_code}: {response.text[:500]}", response.status_code
        )

    @cached_property
    def token_owner(self) -> str | None:
        """The account behind a personal token. GITHUB_TOKEN and an app token
        have none: /user refuses them."""
        try:
            response = self.client.get("/user")
        except httpx.HTTPError:
            return None
        return response.json()["login"] if response.is_success else None

    def pages(self, path: str, params: dict[str, str | int]):
        page = 1
        while True:
            items = self.request("GET", path, params={**params, "per_page": PER_PAGE, "page": page}).json()
            yield from items
            if len(items) < PER_PAGE:
                return
            page += 1


def _tasks(site: str, preview_ref: str | None) -> tuple[str, str]:
    # The task tells this site's previews apart within one environment, so
    # one pull request never touches another's deployment. The second is the
    # task from before the rename, or a deployment made then stays active.
    target = f"{site}/{preview_ref}" if preview_ref else site
    return f"deploy:toon/{target}", f"deploy:plak/{target}"


def _markers(site: str, preview_ref: str) -> tuple[str, str]:
    # The second is the marker from before the rename, or a pull request that
    # was open then gets a second comment.
    return f"<!-- toon-preview {site} {preview_ref} -->", f"<!-- plak-preview {site} {preview_ref} -->"


def _event() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _head_sha(event: dict) -> str:
    # On a pull request GITHUB_SHA is the merge commit, not the pushed one.
    return event.get("pull_request", {}).get("head", {}).get("sha") or os.environ["GITHUB_SHA"]


def _run_url() -> str:
    env = os.environ
    return f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"


def _deactivate(github: GitHub, environment: str, tasks: tuple[str, str], keep: int | None) -> None:
    """Marks this preview's earlier deployments inactive, under either task,
    skipping keep and those already inactive."""
    for task in tasks:
        for deployment in github.pages("/deployments", {"environment": environment, "task": task}):
            if deployment["id"] == keep:
                continue
            statuses = github.request(
                "GET", f"/deployments/{deployment['id']}/statuses", params={"per_page": 1}
            ).json()
            if statuses and statuses[0]["state"] == "inactive":
                continue
            github.request("POST", f"/deployments/{deployment['id']}/statuses", json={"state": "inactive"})


def create_deployment(
    github: GitHub, event: dict, environment: str, site: str, preview_ref: str | None, url: str
) -> None:
    tasks = _tasks(site, preview_ref)
    body: dict = {
        "ref": _head_sha(event),
        "task": tasks[0],
        "environment": environment,
        "description": f"Toon {site}",
        # Without these two GitHub merges the default branch into ref and
        # refuses the deployment while any commit status is not green.
        "auto_merge": False,
        "required_contexts": [],
    }
    if preview_ref:
        body["transient_environment"] = True
        body["production_environment"] = False
    deployment = github.request("POST", "/deployments", json=body).json()
    status: dict = {"state": "success", "environment_url": url, "log_url": _run_url()}
    if preview_ref:
        # GitHub's auto_inactive would also switch off the previews of other
        # pull requests in the same environment; this preview's own earlier
        # deployments are switched off below instead.
        status["auto_inactive"] = False
    github.request("POST", f"/deployments/{deployment['id']}/statuses", json=status)
    if preview_ref:
        _deactivate(github, environment, tasks, keep=deployment["id"])


def _is_ours(github: GitHub, comment: dict) -> bool:
    # Anyone who can comment can start a comment with the marker. Only an app
    # installed on the repository writes as a bot, GITHUB_TOKEN among them.
    author = comment.get("user") or {}
    return author.get("type") == "Bot" or ("login" in author and author["login"] == github.token_owner)


def _find_comment(github: GitHub, number: int, markers: tuple[str, str]) -> dict | None:
    for comment in github.pages(f"/issues/{number}/comments", {}):
        if (comment.get("body") or "").startswith(markers) and _is_ours(github, comment):
            return comment
    return None


def upsert_comment(github: GitHub, number: int, markers: tuple[str, str], text: str, *, create: bool) -> None:
    body = f"{markers[0]}\n{text}"
    comment = _find_comment(github, number, markers)
    if comment is not None:
        github.request("PATCH", f"/issues/comments/{comment['id']}", json={"body": body})
    elif create:
        github.request("POST", f"/issues/{number}/comments", json={"body": body})


def _access(value: str) -> tuple[str, bool, bool]:
    """The access line toon publish writes: `site_team,invitees`, say."""
    base, *extras = value.split(",")
    if base not in ACCESS_BASE_WHO or len(set(extras)) != len(extras) or not set(extras) <= {"keys", "invitees"}:
        raise argparse.ArgumentTypeError(f"not an access: {value!r}")
    return base, "keys" in extras, "invitees" in extras


SIGNED_IN_WHO = {"sso": "anyone with an SSO Rijk account", "site_team": "members of the site and its group"}


def _access_line(access: tuple[str, bool, bool]) -> str:
    base, keys, invitees = access
    if base == "public":
        return "Open to anyone."
    # The ones the server's login_can_help counts: a secret link needs no sign-in.
    signed_in = [SIGNED_IN_WHO[base]] if base in SIGNED_IN_WHO else []
    if invitees:
        signed_in.append("invitees")
    if not signed_in:
        return "Open to anyone with a secret link." if keys else "Open to nobody yet."
    line = f"Open to {' and '.join(signed_in)}, after signing in on Toon"
    return f"{line}, and to anyone with a secret link." if keys else f"{line}."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m toon_cli.github")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "teardown"):
        command = sub.add_parser(name)
        command.add_argument("--site", required=True)
        command.add_argument("--preview-ref", default="", required=name == "teardown")
        command.add_argument("--environment", default="")
        command.add_argument("--comment", action="store_true")
        if name == "publish":
            command.add_argument("--url", required=True)
            command.add_argument("--version-id", required=True)
            command.add_argument("--access", type=_access)
    return parser


def run(argv: list[str], transport: httpx.BaseTransport | None = None) -> int:
    args = _parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: github-token is empty; environment and comment-on-pr need it", file=sys.stderr)
        return 2
    preview_ref = args.preview_ref or None
    event = _event()
    number = event.get("pull_request", {}).get("number")
    client = httpx.Client(
        base_url=os.environ["GITHUB_API_URL"],
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"toon-cli/{VERSION}",
        },
        timeout=30.0,
        transport=transport,
    )
    github = GitHub(client, os.environ["GITHUB_REPOSITORY"])
    try:
        if args.command == "publish":
            if args.environment:
                create_deployment(github, event, args.environment, args.site, preview_ref, args.url)
            if args.comment and number is None:
                print("::notice::comment skipped: this run is not for a pull request")
            elif args.comment:
                what = "Preview" if preview_ref else "Live"
                access = f"{_access_line(args.access)}\n\n" if args.access else ""
                upsert_comment(
                    github,
                    number,
                    _markers(args.site, preview_ref or "live"),
                    f"**{what}:** {args.url}\n\n{access}"
                    f"Commit {_head_sha(event)[:7]}, Toon version `{args.version_id}`.",
                    create=True,
                )
        else:
            if args.environment:
                _deactivate(github, args.environment, _tasks(args.site, preview_ref), keep=None)
            if args.comment and number is not None:
                upsert_comment(
                    github,
                    number,
                    _markers(args.site, preview_ref),
                    "**Preview:** removed.",
                    create=False,
                )
    except GitHubError as error:
        print(f"Error: {error}", file=sys.stderr)
        if error.status in (403, 404):
            print(
                "The workflow needs 'deployments: write' for environment and "
                "'pull-requests: write' for comment-on-pr in its permissions.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point guard, exercised through the action
    sys.exit(run(sys.argv[1:]))
