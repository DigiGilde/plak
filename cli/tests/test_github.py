"""toon_cli.github: the deployment and pull request comment the publish
action reports to GitHub, against a fake GitHub API on a mock transport."""

from __future__ import annotations

import json

import httpx
import pytest
from toon_cli import github

SITE = "team-aurora/website"
PREVIEW_URL = "https://toon.example/team-aurora/website/_preview/pr-42/"
VERSION = "11111111-2222-3333-4444-555555555555"
MARKER = "<!-- toon-preview team-aurora/website pr-42 -->"
OLD_MARKER = "<!-- plak-preview team-aurora/website pr-42 -->"
TASK = "deploy:toon/team-aurora/website/pr-42"
OLD_TASK = "deploy:plak/team-aurora/website/pr-42"
BOT = {"login": "github-actions[bot]", "type": "Bot"}
SOMEONE = {"login": "mallory", "type": "User"}
TOKEN_OWNER = {"login": "robbert", "type": "User"}


def _by_bot(comment_id: int, body: str | None) -> dict:
    return {"id": comment_id, "body": body, "user": BOT}


def _token_without_an_owner(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403, json={"message": "Resource not accessible by integration"})


def _github_unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no route to host", request=request)


def _deployments(by_task: dict[str, list]):
    """The deployment list as GitHub answers it: only those of the task asked for."""
    return lambda request: by_task.get(request.url.params["task"], [])


class FakeGitHub:
    """Answers per (method, path) with the next queued answer; the last one
    stays. An answer that is a function gets the request. Every request is
    recorded, its JSON body decoded."""

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
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(201 if request.method == "POST" else 200, json=answer)

    def on_user(self, *answers) -> None:
        self.answers[("GET", "/user")] = list(answers)

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
    fake.on("GET", "/deployments", _deployments({TASK: [{"id": 7}]}))

    assert _publish_preview(fake, "--environment", "preview") == 0

    [deployment] = fake.sent("POST", "/deployments")
    assert deployment == {
        "ref": "abcdef0123456789",
        "task": TASK,
        "environment": "preview",
        "description": f"Toon {SITE}",
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
    assert fake.requests[0]["headers"]["User-Agent"] == f"toon-cli/{github.VERSION}"


def test_a_new_preview_deployment_switches_off_only_this_previews_earlier_ones(fake, runner_env):
    """auto_inactive is off, so other pull requests keep their preview; the
    list is filtered on this preview's task, and an earlier deployment that
    is already inactive gets no second inactive status."""
    fake.on("POST", "/deployments", {"id": 9})
    fake.on("POST", "/deployments/9/statuses", {})
    fake.on("GET", "/deployments", _deployments({TASK: [{"id": 9}, {"id": 8}, {"id": 5}]}))
    fake.on("GET", "/deployments/8/statuses", [{"state": "success"}])
    fake.on("GET", "/deployments/5/statuses", [{"state": "inactive"}])
    fake.on("POST", "/deployments/8/statuses", {})

    assert _publish_preview(fake, "--environment", "preview") == 0

    listings = [r["params"] for r in fake.requests if r["method"] == "GET" and r["path"].endswith("/deployments")]
    assert listings == [
        {"environment": "preview", "task": task, "per_page": "100", "page": "1"} for task in (TASK, OLD_TASK)
    ]
    assert fake.sent("POST", "/deployments/8/statuses") == [{"state": "inactive"}]
    assert ("POST", "/deployments/5/statuses") not in fake.calls()


def test_an_earlier_deployment_without_any_status_is_switched_off_too(fake, runner_env):
    fake.on("POST", "/deployments", {"id": 9})
    fake.on("POST", "/deployments/9/statuses", {})
    fake.on("GET", "/deployments", _deployments({TASK: [{"id": 4}]}))
    fake.on("GET", "/deployments/4/statuses", [])
    fake.on("POST", "/deployments/4/statuses", {})

    assert _publish_preview(fake, "--environment", "preview") == 0

    assert fake.sent("POST", "/deployments/4/statuses") == [{"state": "inactive"}]


@pytest.mark.parametrize("task", [TASK, OLD_TASK])
def test_the_deployment_list_is_read_past_its_first_page(fake, runner_env, task):
    pages = {"1": [{"id": 1000 + i} for i in range(100)], "2": [{"id": 3}]}
    fake.on("POST", "/deployments", {"id": 1000})
    fake.on("POST", "/deployments/1000/statuses", {})
    fake.on(
        "GET",
        "/deployments",
        lambda request: pages[request.url.params["page"]] if request.url.params["task"] == task else [],
    )
    fake.on("GET", "/deployments/3/statuses", [{"state": "inactive"}])
    for i in range(1, 100):
        fake.on("GET", f"/deployments/{1000 + i}/statuses", [{"state": "inactive"}])

    assert _publish_preview(fake, "--environment", "preview") == 0

    read = [
        r["params"]["page"]
        for r in fake.requests
        if r["path"].endswith("/deployments") and r["method"] == "GET" and r["params"]["task"] == task
    ]
    assert read == ["1", "2"]
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
        "publish", "--site", SITE, "--url", "https://toon.example/team-aurora/website/",
        "--version-id", VERSION, "--environment", "productie",
    )

    assert code == 0
    [deployment] = fake.sent("POST", "/deployments")
    assert deployment["ref"] == "merge0000000000"
    assert deployment["task"] == "deploy:toon/team-aurora/website"
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
    # The url already names the site and the ref; the comment does not repeat them.
    assert comment["body"] == (
        f"{MARKER}\n**Preview:** {PREVIEW_URL}\n\nCommit abcdef0, Toon version `{VERSION}`."
    )
    assert ("POST", "/deployments") not in fake.calls()


def test_a_later_deploy_updates_the_comment_instead_of_adding_one(fake, runner_env):
    """Only a comment that starts with the marker is ours: one that quotes
    it is somebody else's, and editing that would fail on permissions."""
    fake.on(
        "GET",
        "/issues/42/comments",
        [_by_bot(5, f"> {MARKER}\n> quoted"), _by_bot(6, f"{MARKER}\nold")],
    )
    fake.on("PATCH", "/issues/comments/6", {})

    assert _publish_preview(fake, "--comment") == 0

    [update] = fake.sent("PATCH", "/issues/comments/6")
    assert PREVIEW_URL in update["body"]
    assert fake.calls("POST") == []
    assert ("GET", "/user") not in fake.calls()


def test_a_comment_outside_a_pull_request_is_skipped_with_a_notice(
    fake, runner_env, monkeypatch, capsys
):
    monkeypatch.delenv("GITHUB_EVENT_PATH")

    code = _run(
        fake,
        "publish", "--site", SITE, "--url", "https://toon.example/team-aurora/website/",
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
        "publish", "--site", SITE, "--url", "https://toon.example/team-aurora/website/",
        "--version-id", VERSION, "--comment",
    )

    assert code == 0
    [comment] = fake.sent("POST", "/issues/42/comments")
    assert comment["body"].startswith(
        "<!-- toon-preview team-aurora/website live -->\n**Live:** https://toon.example/team-aurora/website/\n"
    )


@pytest.mark.parametrize(
    ("access", "line"),
    [
        ("public", "Open to anyone."),
        ("public,keys,invitees", "Open to anyone."),
        ("sso", "Open to anyone with an SSO Rijk account, after signing in on Toon."),
        ("site_team", "Open to members of the site and its group, after signing in on Toon."),
        (
            "site_team,keys",
            (
                "Open to members of the site and its group, after signing in on Toon, "
                "and to anyone with a secret link."
            ),
        ),
        (
            "site_team,invitees",
            "Open to members of the site and its group and invitees, after signing in on Toon.",
        ),
        ("nobody,invitees", "Open to invitees, after signing in on Toon."),
        # A secret link is the only way in, so signing in would not help.
        ("nobody,keys", "Open to anyone with a secret link."),
        ("nobody", "Open to nobody yet."),
    ],
)
def test_the_comment_says_whether_the_link_needs_a_sign_in(fake, runner_env, access, line):
    fake.on("GET", "/issues/42/comments", [])
    fake.on("POST", "/issues/42/comments", {"id": 3})

    assert _publish_preview(fake, "--comment", "--access", access) == 0

    [comment] = fake.sent("POST", "/issues/42/comments")
    assert f"{PREVIEW_URL}\n\n{line}\n\nCommit" in comment["body"]


def test_without_access_the_comment_leaves_the_sign_in_out(fake, runner_env):
    """A server from before the access field: the link still goes in."""
    fake.on("GET", "/issues/42/comments", [])
    fake.on("POST", "/issues/42/comments", {"id": 3})

    assert _publish_preview(fake, "--comment") == 0

    [comment] = fake.sent("POST", "/issues/42/comments")
    assert f"{PREVIEW_URL}\n\nCommit" in comment["body"]
    assert "Open to" not in comment["body"]


@pytest.mark.parametrize("access", ["everyone", "public,admins", "sso,keys,keys", ""])
def test_an_access_value_that_is_not_ours_is_refused(fake, runner_env, capsys, access):
    with pytest.raises(SystemExit) as exit_info:
        _publish_preview(fake, "--comment", "--access", access)

    assert exit_info.value.code == 2
    assert "--access" in capsys.readouterr().err
    assert fake.requests == []


# --- teardown ---------------------------------------------------------------


def test_teardown_switches_off_the_deployments_and_marks_the_comment(fake, runner_env):
    fake.on("GET", "/deployments", _deployments({TASK: [{"id": 8}]}))
    fake.on("GET", "/deployments/8/statuses", [{"state": "success"}])
    fake.on("POST", "/deployments/8/statuses", {})
    fake.on("GET", "/issues/42/comments", [_by_bot(6, f"{MARKER}\nold")])
    fake.on("PATCH", "/issues/comments/6", {})

    code = _run(
        fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--environment", "preview", "--comment"
    )

    assert code == 0
    assert fake.sent("POST", "/deployments/8/statuses") == [{"state": "inactive"}]
    [update] = fake.sent("PATCH", "/issues/comments/6")
    assert update["body"] == f"{MARKER}\n**Preview:** removed."


def test_teardown_places_no_comment_where_there_was_none(fake, runner_env):
    fake.on("GET", "/issues/42/comments", [])

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--comment") == 0

    assert fake.calls() == [("GET", "/issues/42/comments")]


def test_teardown_outside_a_pull_request_leaves_comments_alone(fake, runner_env, monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH")

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--comment") == 0

    assert fake.requests == []


# --- what the action wrote before the rename --------------------------------


def test_a_new_preview_deployment_switches_off_the_ones_under_the_old_task(fake, runner_env):
    """A pull request open across the rename: what it deployed then has the
    old task, and would stay active beside the new deployment."""
    fake.on("POST", "/deployments", {"id": 9})
    fake.on("POST", "/deployments/9/statuses", {})
    fake.on("GET", "/deployments", _deployments({TASK: [{"id": 9}], OLD_TASK: [{"id": 8}, {"id": 5}]}))
    fake.on("GET", "/deployments/8/statuses", [{"state": "success"}])
    fake.on("GET", "/deployments/5/statuses", [{"state": "inactive"}])
    fake.on("POST", "/deployments/8/statuses", {})

    assert _publish_preview(fake, "--environment", "preview") == 0

    [deployment] = fake.sent("POST", "/deployments")
    assert deployment["task"] == TASK
    assert fake.sent("POST", "/deployments/8/statuses") == [{"state": "inactive"}]
    assert ("POST", "/deployments/5/statuses") not in fake.calls()


def test_teardown_switches_off_the_deployments_under_both_tasks(fake, runner_env):
    fake.on("GET", "/deployments", _deployments({TASK: [{"id": 9}], OLD_TASK: [{"id": 8}]}))
    for deployment in (8, 9):
        fake.on("GET", f"/deployments/{deployment}/statuses", [{"state": "success"}])
        fake.on("POST", f"/deployments/{deployment}/statuses", {})

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--environment", "preview") == 0

    assert fake.sent("POST", "/deployments/8/statuses") == [{"state": "inactive"}]
    assert fake.sent("POST", "/deployments/9/statuses") == [{"state": "inactive"}]


@pytest.mark.parametrize(
    "other",
    [
        "deploy:plak/team-aurora/website/pr-4",
        "deploy:plak/team-aurora/website/pr-421",
        "deploy:plak/team-aurora/website-2/pr-42",
        "deploy:plak/team-aurora/website",
    ],
    ids=["shorter-ref", "longer-ref", "other-site", "live"],
)
def test_an_old_deployment_of_something_else_stays_active(fake, runner_env, other):
    fake.on("GET", "/deployments", _deployments({other: [{"id": 8}]}))

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--environment", "preview") == 0

    assert [r["params"]["task"] for r in fake.requests] == [TASK, OLD_TASK]
    assert fake.calls("POST") == []


def test_a_comment_under_the_old_marker_is_updated_and_takes_the_new_one(fake, runner_env):
    """A pull request open across the rename keeps its one comment, and from
    then on it carries the new marker."""
    fake.on("GET", "/issues/42/comments", [_by_bot(6, f"{OLD_MARKER}\n**Preview:** old")])
    fake.on("PATCH", "/issues/comments/6", {})

    assert _publish_preview(fake, "--comment") == 0

    [update] = fake.sent("PATCH", "/issues/comments/6")
    assert update["body"] == f"{MARKER}\n**Preview:** {PREVIEW_URL}\n\nCommit abcdef0, Toon version `{VERSION}`."
    assert fake.calls("POST") == []


def test_a_live_comment_under_the_old_marker_is_updated_too(fake, runner_env):
    fake.on("GET", "/issues/42/comments", [_by_bot(6, "<!-- plak-preview team-aurora/website live -->\nold")])
    fake.on("PATCH", "/issues/comments/6", {})

    code = _run(
        fake,
        "publish", "--site", SITE, "--url", "https://toon.example/team-aurora/website/",
        "--version-id", VERSION, "--comment",
    )

    assert code == 0
    [update] = fake.sent("PATCH", "/issues/comments/6")
    assert update["body"].startswith("<!-- toon-preview team-aurora/website live -->\n**Live:** ")
    assert fake.calls("POST") == []


def test_teardown_marks_a_comment_under_the_old_marker_removed(fake, runner_env):
    fake.on("GET", "/issues/42/comments", [_by_bot(6, f"{OLD_MARKER}\nold")])
    fake.on("PATCH", "/issues/comments/6", {})

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--comment") == 0

    assert fake.sent("PATCH", "/issues/comments/6") == [{"body": f"{MARKER}\n**Preview:** removed."}]


_NOT_OUR_OLD_COMMENTS = [
    f"> {OLD_MARKER}\n> quoted",
    f"See {OLD_MARKER}",
    f" {OLD_MARKER}\nold",
    "<!-- plak-preview team-aurora/website pr-4 -->\nold",
    "<!-- plak-preview team-aurora/website pr-421 -->\nold",
    "<!-- plak-preview team-aurora/website-2 pr-42 -->\nold",
    "<!-- plak-preview team-aurora/website live -->\nold",
    "<!-- PLAK-PREVIEW team-aurora/website pr-42 -->\nold",
    "<!-- plak-preview team-aurora/website pr-42\nold",
    "",
    None,
]


@pytest.mark.parametrize("body", _NOT_OUR_OLD_COMMENTS)
def test_a_comment_that_only_resembles_the_old_marker_is_left_alone(fake, runner_env, body):
    """Somebody else's comment, or that of another preview or site: editing
    it would fail on permissions or overwrite the wrong link."""
    fake.on("GET", "/issues/42/comments", [_by_bot(6, body)])
    fake.on("POST", "/issues/42/comments", {"id": 7})

    assert _publish_preview(fake, "--comment") == 0

    assert fake.calls("PATCH") == []
    [comment] = fake.sent("POST", "/issues/42/comments")
    assert comment["body"].startswith(f"{MARKER}\n")


@pytest.mark.parametrize("body", _NOT_OUR_OLD_COMMENTS)
def test_teardown_leaves_a_comment_that_only_resembles_the_old_marker_alone(fake, runner_env, body):
    fake.on("GET", "/issues/42/comments", [_by_bot(6, body)])

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--comment") == 0

    assert fake.calls() == [("GET", "/issues/42/comments")]


def test_with_comments_under_both_markers_the_first_is_updated_and_none_is_added(fake, runner_env):
    fake.on(
        "GET",
        "/issues/42/comments",
        [_by_bot(6, f"{OLD_MARKER}\nold"), _by_bot(7, f"{MARKER}\nnewer")],
    )
    fake.on("PATCH", "/issues/comments/6", {})

    assert _publish_preview(fake, "--comment") == 0

    assert [call for call in fake.calls() if call[0] != "GET"] == [("PATCH", "/issues/comments/6")]


# --- whose comment -----------------------------------------------------------


@pytest.mark.parametrize("who_is_the_token", [_token_without_an_owner, _github_unreachable])
def test_a_comment_by_someone_else_with_the_marker_is_left_alone(fake, runner_env, who_is_the_token):
    """Anyone who can comment can start one with the marker. Updating it would
    put the link under their name, and no comment of ours would appear."""
    fake.on_user(who_is_the_token)
    fake.on("GET", "/issues/42/comments", [{"id": 6, "body": f"{MARKER}\nold", "user": SOMEONE}])
    fake.on("POST", "/issues/42/comments", {"id": 7})

    assert _publish_preview(fake, "--comment") == 0

    assert fake.calls("PATCH") == []
    [comment] = fake.sent("POST", "/issues/42/comments")
    assert comment["body"].startswith(f"{MARKER}\n**Preview:** {PREVIEW_URL}")


def test_teardown_leaves_a_comment_by_someone_else_alone(fake, runner_env):
    fake.on_user(_token_without_an_owner)
    fake.on("GET", "/issues/42/comments", [{"id": 6, "body": f"{MARKER}\nold", "user": SOMEONE}])

    assert _run(fake, "teardown", "--site", SITE, "--preview-ref", "pr-42", "--comment") == 0

    assert [call for call in fake.calls() if call[0] != "GET"] == []


def test_a_comment_by_a_deleted_account_is_left_alone(fake, runner_env):
    """GitHub answers user: null for it, and a token without an owner must not
    count as that same nobody."""
    fake.on_user(_token_without_an_owner)
    fake.on("GET", "/issues/42/comments", [{"id": 6, "body": f"{MARKER}\nold", "user": None}])
    fake.on("POST", "/issues/42/comments", {"id": 7})

    assert _publish_preview(fake, "--comment") == 0

    assert fake.calls("PATCH") == []
    assert len(fake.sent("POST", "/issues/42/comments")) == 1


def test_a_personal_token_updates_its_own_comment_and_asks_who_it_is_once(fake, runner_env):
    fake.on_user(TOKEN_OWNER)
    fake.on(
        "GET",
        "/issues/42/comments",
        [
            {"id": 5, "body": f"{MARKER}\nfirst", "user": SOMEONE},
            {"id": 6, "body": f"{MARKER}\nsecond", "user": SOMEONE},
            {"id": 7, "body": f"{MARKER}\nold", "user": TOKEN_OWNER},
        ],
    )
    fake.on("PATCH", "/issues/comments/7", {})

    assert _publish_preview(fake, "--comment") == 0

    assert [call for call in fake.calls() if call[0] != "GET"] == [("PATCH", "/issues/comments/7")]
    assert fake.calls("GET").count(("GET", "/user")) == 1


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
