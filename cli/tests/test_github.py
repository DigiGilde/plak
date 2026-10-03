"""plak_cli.github: the deployment and pull request comment the publish
action reports to GitHub, against a fake GitHub API on a mock transport."""

from __future__ import annotations

import json

import httpx
import pytest
from plak_cli import github

SITE = "team-aurora/website"
PREVIEW_URL = "https://plak.example/team-aurora/website/_preview/pr-42/"
VERSION = "11111111-2222-3333-4444-555555555555"
MARKER = "<!-- plak-preview team-aurora/website pr-42 -->"
TASK = "deploy:plak/team-aurora/website/pr-42"


class FakeGitHub:
    """Answers per (method, path) with the next queued answer; the last one
    stays. Every request is recorded, its JSON body decoded."""

    def __init__(self) -> None:
        self.answers: dict[tuple[str, str], list] = {}
        self.requests: list[dict] = []

    def on(self, method: str, path: str, *answers) -> None:
        self.answers[(method, f"/repos/digigilde/website{path}")] = list(answers)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "json": body,
                "headers": request.headers,
            }
        )
        queue = self.answers[(request.method, request.url.path)]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(201 if request.method == "POST" else 200, json=answer)

    def calls(self, method: str | None = None) -> list[tuple[str, str]]:
        return [
            (r["method"], r["path"].removeprefix("/repos/digigilde/website"))
            for r in self.requests
            if method is None or r["method"] == method
        ]

    def sent(self, method: str, path: str) -> list:
        return [
            r["json"]
            for r in self.requests
            if r["method"] == method and r["path"] == f"/repos/digigilde/website{path}"
        ]


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def runner_env(monkeypatch, tmp_path):
    """A GitHub runner on a pull_request event for PR 42."""
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42, "head": {"sha": "abcdef0123456789"}}}))
    monkeypatch.setenv("GITHUB_TOKEN", "ghs-token")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.test")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "digigilde/website")
    monkeypatch.setenv("GITHUB_RUN_ID", "987")
    monkeypatch.setenv("GITHUB_SHA", "merge0000000000")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    return event


def _run(fake: FakeGitHub, *argv: str) -> int:
    return github.run(list(argv), transport=httpx.MockTransport(fake.handler))


def _publish_preview(fake: FakeGitHub, *extra: str) -> int:
    return _run(
        fake,
        "publish",
        "--site", SITE,
        "--url", PREVIEW_URL,
        "--version-id", VERSION,
        "--preview-ref", "pr-42",
        *extra,
    )


# --- deployment -------------------------------------------------------------


def test_preview_deployment_points_at_the_pushed_commit_and_the_preview(fake, runner_env):
    fake.on("POST", "/deployments", {"id": 7})
    fake.on("POST", "/deployments/7/statuses", {})
    fake.on("GET", "/deployments", [{"id": 7}])

    assert _publish_preview(fake, "--environment", "preview") == 0

    [deployment] = fake.sent("POST", "/deployments")
    assert deployment == {
        "ref": "abcdef0123456789",
        "task": TASK,
        "environment": "preview",
        "description": f"Plak {SITE}",
        "auto_merge": False,
        "required_contexts": [],
        "transient_environment": True,
        "production_environment": False,
    }
    [status] = fake.sent("POST", "/deployments/7/statuses")
    assert status == {
        "state": "success",
        "environment_url": PREVIEW_URL,
        "log_url": "https://github.test/digigilde/website/actions/runs/987",
        "auto_inactive": False,
    }
    assert fake.requests[0]["headers"]["Authorization"] == "Bearer ghs-token"
    assert fake.requests[0]["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert fake.requests[0]["headers"]["User-Agent"] == f"plak-cli/{github.VERSION}"


def test_a_new_preview_deployment_switches_off_only_this_previews_earlier_ones(fake, runner_env):
    """auto_inactive is off, so other pull requests keep their preview; the
    list is filtered on this preview's task, and an earlier deployment that
    is already inactive gets no second inactive status."""
    fake.on("POST", "/deployments", {"id": 9})
    fake.on("POST", "/deployments/9/statuses", {})
    fake.on("GET", "/deployments", [{"id": 9}, {"id": 8}, {"id": 5}])
    fake.on("GET", "/deployments/8/statuses", [{"state": "success"}])
    fake.on("GET", "/deployments/5/statuses", [{"state": "inactive"}])
    fake.on("POST", "/deployments/8/statuses", {})

    assert _publish_preview(fake, "--environment", "preview") == 0

    [listing] = [r for r in fake.requests if r["method"] == "GET" and r["path"].endswith("/deployments")]
    assert listing["params"] == {"environment": "preview", "task": TASK, "per_page": "100", "page": "1"}
    assert fake.sent("POST", "/deployments/8/statuses") == [{"state": "inactive"}]
    assert ("POST", "/deployments/5/statuses") not in fake.calls()


def test_an_earlier_deployment_without_any_status_is_switched_off_too(fake, runner_env):
    fake.on("POST", "/deployments", {"id": 9})
    fake.on("POST", "/deployments/9/statuses", {})
    fake.on("GET", "/deployments", [{"id": 4}])
    fake.on("GET", "/deployments/4/statuses", [])
    fake.on("POST", "/deployments/4/statuses", {})

    assert _publish_preview(fake, "--environment", "preview") == 0

    assert fake.sent("POST", "/deployments/4/statuses") == [{"state": "inactive"}]


def test_the_deployment_list_is_read_past_its_first_page(fake, runner_env):
    fake.on("POST", "/deployments", {"id": 1000})
    fake.on("POST", "/deployments/1000/statuses", {})
    fake.on("GET", "/deployments", [{"id": 1000 + i} for i in range(100)], [{"id": 3}])
    fake.on("GET", "/deployments/3/statuses", [{"state": "inactive"}])
    for i in range(1, 100):
        fake.on("GET", f"/deployments/{1000 + i}/statuses", [{"state": "inactive"}])

    assert _publish_preview(fake, "--environment", "preview") == 0

    pages = [r["params"]["page"] for r in fake.requests if r["path"].endswith("/deployments") and r["method"] == "GET"]
    assert pages == ["1", "2"]
    assert ("GET", "/deployments/3/statuses") in fake.calls()


def test_a_live_deployment_leaves_production_and_auto_inactive_to_github(
    fake, runner_env, monkeypatch, tmp_path
):
    """A push event carries no pull_request: the ref is GITHUB_SHA, and
    GitHub's own auto_inactive replaces the previous live deployment."""
    push = tmp_path / "push.json"
    push.write_text(json.dumps({"ref": "refs/heads/main"}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(push))
    fake.on("POST", "/deployments", {"id": 3})
    fake.on("POST", "/deployments/3/statuses", {})

    code = _run(
        fake,
        "publish", "--site", SITE, "--url", "https://plak.example/team-aurora/website/",
        "--version-id", VERSION, "--environment", "productie",
    )

    assert code == 0
    [deployment] = fake.sent("POST", "/deployments")
    assert deployment["ref"] == "merge0000000000"
    assert deployment["task"] == "deploy:plak/team-aurora/website"
    assert "transient_environment" not in deployment
    assert "production_environment" not in deployment
    [status] = fake.sent("POST", "/deployments/3/statuses")
    assert "auto_inactive" not in status
    assert fake.calls("GET") == []


# --- comment ----------------------------------------------------------------


def test_the_first_deploy_of_a_pull_request_places_a_comment(fake, runner_env):
    fake.on("GET", "/issues/42/comments", [{"id": 1, "body": None}, {"id": 2, "body": "Mooi!"}])
    fake.on("POST", "/issues/42/comments", {"id": 3})

    assert _publish_preview(fake, "--comment") == 0

    [comment] = fake.sent("POST", "/issues/42/comments")
    assert comment["body"].startswith(MARKER + "\n")
    assert PREVIEW_URL in comment["body"]
    assert "Preview `pr-42` of `team-aurora/website`" in comment["body"]
    assert VERSION in comment["body"]
    assert "commit abcdef0" in comment["body"]
    assert ("POST", "/deployments") not in fake.calls()


def test_a_later_deploy_updates_the_comment_instead_of_adding_one(fake, runner_env):
    """Only a comment that starts with the marker is ours: one that quotes
    it is somebody else's, and editing that would fail on permissions."""
    fake.on(
        "GET",
        "/issues/42/comments",
        [{"id": 5, "body": f"> {MARKER}\n> quoted"}, {"id": 6, "body": f"{MARKER}\nold"}],
    )
    fake.on("PATCH", "/issues/comments/6", {})

    assert _publish_preview(fake, "--comment") == 0

    [update] = fake.sent("PATCH", "/issues/comments/6")
    assert PREVIEW_URL in update["body"]
    assert fake.calls("POST") == []


def test_a_comment_outside_a_pull_request_is_skipped_with_a_notice(
    fake, runner_env, monkeypatch, capsys
):
    monkeypatch.delenv("GITHUB_EVENT_PATH")

    code = _run(
        fake,
        "publish", "--site", SITE, "--url", "https://plak.example/team-aurora/website/",
        "--version-id", VERSION, "--comment",
    )

    assert code == 0
    assert "::notice::comment skipped" in capsys.readouterr().out
    assert fake.requests == []


def test_a_live_deploy_on_a_pull_request_comments_under_its_own_marker(fake, runner_env):
    fake.on("GET", "/issues/42/comments", [])
    fake.on("POST", "/issues/42/comments", {"id": 3})

    code = _run(
        fake,
        "publish", "--site", SITE, "--url", "https://plak.example/team-aurora/website/",
        "--version-id", VERSION, "--comment",
    )

    assert code == 0
    [comment] = fake.sent("POST", "/issues/42/comments")
    assert comment["body"].startswith("<!-- plak-preview team-aurora/website live -->\nLive site of")


@pytest.mark.parametrize(
    ("access", "line"),
    [
        ("public", "Anyone can open it, without signing in."),
        ("sso", "Sign in to open it. Who can see it: anyone who signs in with SSO Rijk."),
        (
            "site_team,keys",
            (
                "Sign in to open it. Who can see it: members of the site and its group, "
                "anyone with a secret link."
            ),
        ),
        ("nobody,invitees", "Sign in to open it. Who can see it: invitees, once signed in."),
        # A secret link is the only way in, so signing in would not help.
        ("nobody,keys", "Who can see it: anyone with a secret link."),
        ("nobody", "Who can see it: nobody yet."),
    ],
)
def test_the_comment_says_whether_the_link_needs_a_sign_in(fake, runner_env, access, line):
    fake.on("GET", "/issues/42/comments", [])
    fake.on("POST", "/issues/42/comments", {"id": 3})

    assert _publish_preview(fake, "--comment", "--access", access) == 0

    [comment] = fake.sent("POST", "/issues/42/comments")
    assert f"{PREVIEW_URL}\n\n{line}\n\nVersion" in comment["body"]


def test_without_access_the_comment_leaves_the_sign_in_out(fake, runner_env):
    """A Plak from before the access field: the link still goes in."""
    fake.on("GET", "/issues/42/comments", [])
    fake.on("POST", "/issues/42/comments", {"id": 3})

    assert _publish_preview(fake, "--comment") == 0

    [comment] = fake.sent("POST", "/issues/42/comments")
    assert f"{PREVIEW_URL}\n\nVersion" in comment["body"]
    assert "Who can see it" not in comment["body"]


@pytest.mark.parametrize("access", ["everyone", "public,admins", "sso,keys,keys", ""])
def test_an_access_value_that_is_not_ours_is_refused(fake, runner_env, capsys, access):
    with pytest.raises(SystemExit) as exit_info:
        _publish_preview(fake, "--comment", "--access", access)

    assert exit_info.value.code == 2
    assert "--access" in capsys.readouterr().err
    assert fake.requests == []


# --- teardown ---------------------------------------------------------------


def test_teardown_switches_off_the_deployments_and_marks_the_comment(fake, runner_env):
    fake.on("GET", "/deployments", [{"id": 8}])
    fake.on("GET", "/deployments/8/statuses", [{"state": "success"}])
    fake.on("POST", "/deployments/8/statuses", {})
    fake.on("GET", "/issues/42/comments", [{"id": 6, "body": f"{MARKER}\nold"}])
    fake.on("PATCH", "/issues/comments/6", {})

    code = _run(
        fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--environment", "preview", "--comment"
    )

    assert code == 0
    assert fake.sent("POST", "/deployments/8/statuses") == [{"state": "inactive"}]
    [update] = fake.sent("PATCH", "/issues/comments/6")
    assert update["body"] == f"{MARKER}\nPreview `pr-42` of `team-aurora/website` is removed."


def test_teardown_places_no_comment_where_there_was_none(fake, runner_env):
    fake.on("GET", "/issues/42/comments", [])

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--comment") == 0

    assert fake.calls() == [("GET", "/issues/42/comments")]


def test_teardown_outside_a_pull_request_leaves_comments_alone(fake, runner_env, monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH")

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--comment") == 0

    assert fake.requests == []


# --- refusals ---------------------------------------------------------------


def test_an_empty_token_is_refused_before_any_request(fake, runner_env, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "")

    assert _publish_preview(fake, "--comment") == 2

    assert "github-token is empty" in capsys.readouterr().err
    assert fake.requests == []


@pytest.mark.parametrize("status", [403, 404])
def test_a_missing_permission_names_the_permissions_to_grant(fake, runner_env, capsys, status):
    """GitHub answers 403 for a token without the permission and 404 where
    the repository is not visible to it: both point at permissions."""
    fake.on(
        "POST",
        "/deployments",
        httpx.Response(status, json={"message": "Resource not accessible by integration"}),
    )

    assert _publish_preview(fake, "--environment", "preview") == 1

    err = capsys.readouterr().err
    assert f"POST /repos/digigilde/website/deployments answered {status}" in err
    assert "Resource not accessible by integration" in err
    assert "'deployments: write'" in err
    assert "'pull-requests: write'" in err


def test_another_api_error_fails_without_the_permission_hint(fake, runner_env, capsys):
    fake.on("GET", "/issues/42/comments", httpx.Response(502, text="Bad gateway"))

    assert _publish_preview(fake, "--comment") == 1

    err = capsys.readouterr().err
    assert "answered 502: Bad gateway" in err
    assert "permissions" not in err


def test_an_unreachable_api_fails_the_step(runner_env, capsys):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    code = github.run(
        ["publish", "--site", SITE, "--url", PREVIEW_URL, "--version-id", VERSION, "--comment"],
        transport=httpx.MockTransport(unreachable),
    )

    assert code == 1
    assert "could not reach the GitHub API: connection refused" in capsys.readouterr().err
