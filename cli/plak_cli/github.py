"""Reports a Plak deploy back to GitHub for the publish action: a deployment
in an environment, and a comment on the pull request with the link.

Not a `plak` subcommand: actions/publiceer runs it as `python -m
plak_cli.github`, with the runner's GITHUB_* variables in the environment.

Usage:
    python -m plak_cli.github publish --site <group/site> --url <url> \
        --version-id <id> [--preview-ref <ref>] [--environment <name>] [--comment]
    python -m plak_cli.github teardown --site <group/site> --preview-ref <ref> \
        [--environment <name>] [--comment]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

from plak_cli import VERSION

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

    def pages(self, path: str, params: dict[str, str | int]):
        page = 1
        while True:
            items = self.request("GET", path, params={**params, "per_page": PER_PAGE, "page": page}).json()
            yield from items
            if len(items) < PER_PAGE:
                return
            page += 1


def _task(site: str, preview_ref: str | None) -> str:
    # The task tells this site's previews apart within one environment, so
    # one pull request never touches another's deployment.
    return f"deploy:plak/{site}/{preview_ref}" if preview_ref else f"deploy:plak/{site}"


def _marker(site: str, preview_ref: str) -> str:
    return f"<!-- plak-preview {site} {preview_ref} -->"


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


def _deactivate(github: GitHub, environment: str, task: str, keep: int | None) -> None:
    """Marks this preview's earlier deployments inactive, skipping keep and
    those already inactive."""
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
    task = _task(site, preview_ref)
    body: dict = {
        "ref": _head_sha(event),
        "task": task,
        "environment": environment,
        "description": f"Plak {site}",
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
        _deactivate(github, environment, task, keep=deployment["id"])


def _find_comment(github: GitHub, number: int, marker: str) -> dict | None:
    for comment in github.pages(f"/issues/{number}/comments", {}):
        if (comment.get("body") or "").startswith(marker):
            return comment
    return None


def upsert_comment(github: GitHub, number: int, marker: str, text: str, *, create: bool) -> None:
    body = f"{marker}\n{text}"
    comment = _find_comment(github, number, marker)
    if comment is not None:
        github.request("PATCH", f"/issues/comments/{comment['id']}", json={"body": body})
    elif create:
        github.request("POST", f"/issues/{number}/comments", json={"body": body})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m plak_cli.github")
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
            "User-Agent": f"plak-cli/{VERSION}",
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
                what = f"Preview `{preview_ref}`" if preview_ref else "Live site"
                upsert_comment(
                    github,
                    number,
                    _marker(args.site, preview_ref or "live"),
                    f"{what} of `{args.site}` is published: {args.url}\n\n"
                    f"Version `{args.version_id}`, commit {_head_sha(event)[:7]}.",
                    create=True,
                )
        else:
            if args.environment:
                _deactivate(github, args.environment, _task(args.site, preview_ref), keep=None)
            if args.comment and number is not None:
                upsert_comment(
                    github,
                    number,
                    _marker(args.site, preview_ref),
                    f"Preview `{preview_ref}` of `{args.site}` is removed.",
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
