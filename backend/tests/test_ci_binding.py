"""CI deploys bound to the site id (api/deploys.py, ci/trust.py; spec §6.3).

The decision table, cell by cell, for a deploy and for a preview teardown:
a token bound to site Y or without a site id, an address that leads to Y, to
another site X or to nothing, the token's repository linked to which of
them, and the links requiring a site id or not.

What the table holds: a token bound to Y is used on Y or not at all, and is
decided on Y first, whatever the address. A token whose repository is not
linked to Y gets the same 401 at every address, Y's own included, so the
address tells it nothing. The repository linked to Y deploys at Y's address,
and anywhere else learns where Y is now (409 SITE_MOVED). A token without a
site id deploys only where the link still accepts one, and is refused with
CI_SITE_ID_REQUIRED where it does not. No answer carries a site id.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from helpers_ci import AUDIENCE, FORGEJO_HOST, MockCi
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_deploy_api import BASE_URL, _bearer, _cli_login, _make_app, _make_settings, _upload

from plak.constants import AccessBase, Role
from plak.models.audit import AuditLogEntry
from plak.models.ci import CiProvider, SiteRepository
from plak.models.identity import Group, GroupMember, Member, MemberStatus
from plak.models.publication import Preview, Site, Version, VersionTarget
from plak.models.slugs import SiteSlug

# The token's repository, R, and another one, R2, both on GitHub.
REPOSITORY = {"owner": "minbzk", "repo": "website", "repository_id": 1001, "owner_id": 2002}
OTHER_REPOSITORY = {"owner": "ander", "repo": "project", "repository_id": 5005, "owner_id": 6006}

GROUP = "team-aurora"
# Y is the site a bound token names, X another site in the same group, and
# `none` an address with no site at all.
SLUGS = {"y": "website", "x": "docs", "none": "verdwenen"}

PREVIEW_REF = "pr-7"


@dataclass(frozen=True)
class Binding:
    app: object
    factory: async_sessionmaker[AsyncSession]
    ci: MockCi
    group: Group
    y: Site
    x: Site
    member: Member

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=BASE_URL, follow_redirects=False
        )

    def token(self, bound: bool, **claims: object) -> str:
        if bound:
            claims.setdefault("aud", f"{AUDIENCE}/-/sites/{self.y.id}")
        return self.ci.token(**claims)

    async def link(self, site: Site, repository: dict, *, required: bool, provider=CiProvider.GITHUB) -> None:
        host = "https://github.com" if provider == CiProvider.GITHUB else FORGEJO_HOST
        async with self.factory() as db:
            db.add(
                SiteRepository(
                    site_id=site.id,
                    provider=provider,
                    host=host,
                    live_branch="main",
                    site_id_required=required,
                    **repository,
                )
            )
            await db.commit()

    async def add_preview(self, site: Site) -> None:
        async with self.factory() as db:
            version = Version(
                site_id=site.id,
                target=VersionTarget.PREVIEW,
                storage_ref=f"{site.id}/{uuid.uuid4()}",
                ci_repository="github.com/minbzk/website",
            )
            db.add(version)
            await db.flush()
            db.add(Preview(site_id=site.id, ref=PREVIEW_REF, version_id=version.id))
            await db.commit()

    async def site_ids(self) -> list[str]:
        async with self.factory() as db:
            return [str(site_id) for site_id in await db.scalars(select(Site.id))]

    async def audit_rows(self) -> list[AuditLogEntry]:
        async with self.factory() as db:
            return list(await db.scalars(select(AuditLogEntry).order_by(AuditLogEntry.occurred_at)))

    async def versions_per_site(self) -> dict[uuid.UUID, int]:
        async with self.factory() as db:
            rows = await db.execute(
                select(Version.site_id, func.count()).where(Version.target == VersionTarget.LIVE).group_by(
                    Version.site_id
                )
            )
            return dict(rows.all())

    async def previews(self) -> set[uuid.UUID]:
        async with self.factory() as db:
            return set(await db.scalars(select(Preview.site_id)))


@pytest_asyncio.fixture
async def binding(tmp_path: Path, migrated_dsn: str) -> AsyncIterator[Binding]:
    settings = _make_settings(tmp_path, migrated_dsn)
    ci = MockCi()
    ci.add_forgejo("minbzk", "website", 1001, 2002)
    app = _make_app(settings, ci)
    factory = app.state.session_factory
    group = Group(id=uuid.uuid4(), slug=GROUP, name="Team Aurora", default_access_base=AccessBase.PUBLIC)
    y = Site(id=uuid.uuid4(), group_id=group.id, slug=SLUGS["y"], title="Website", access_base=AccessBase.PUBLIC)
    x = Site(id=uuid.uuid4(), group_id=group.id, slug=SLUGS["x"], title="Docs", access_base=AccessBase.PUBLIC)
    member = Member(id=uuid.uuid4(), sso_subject="sub-redacteur", email="r@example.org", status=MemberStatus.ACTIVE)
    try:
        async with factory() as db:
            db.add_all([group, member])
            await db.flush()
            db.add_all([y, x, GroupMember(group_id=group.id, member_id=member.id, role=Role.EDITOR)])
            await db.commit()
        yield Binding(app=app, factory=factory, ci=ci, group=group, y=y, x=x, member=member)
    finally:
        await app.state.engine.dispose()


# -- The decision table -----------------------------------------------------
#
# links: which sites the token's repository R is linked to; every other site
# of the two is linked to R2. required: the value of site_id_required on all
# of those links. A bound token is decided on Y before the address counts:
# not linked to Y is the one 401 everywhere, linked to Y is Y or SITE_MOVED.

LINKS = {"both": {"y", "x"}, "y": {"y"}, "x": {"x"}, "none": set()}


def _expected(bound: bool, address: str, links: str, required: bool) -> tuple[int, str | None]:
    linked = LINKS[links]
    if bound:
        if "y" not in linked:
            return 401, "CI_AUDIENCE_MISMATCH"
        return (200, None) if address == "y" else (409, "SITE_MOVED")
    if address == "none":
        return 404, "UNKNOWN_SITE"
    if address not in linked:
        return 403, "CI_REPOSITORY_NOT_TRUSTED"
    return (403, "CI_SITE_ID_REQUIRED") if required else (200, None)


GRID = list(itertools.product([True, False], ["y", "x", "none"], list(LINKS), [True, False]))


def _cell_id(cell: tuple) -> str:
    bound, address, links, required = cell
    return f"{'bound' if bound else 'unbound'}-to_{address}-linked_{links}-{'required' if required else 'exempt'}"


async def _set_up_cell(binding: Binding, links: str, required: bool) -> None:
    for name, site in (("y", binding.y), ("x", binding.x)):
        repository = REPOSITORY if name in LINKS[links] else OTHER_REPOSITORY
        await binding.link(site, repository, required=required)


async def _assert_no_site_id_in(binding: Binding, response: httpx.Response) -> None:
    for site_id in await binding.site_ids():
        assert site_id not in response.text
        assert site_id not in str(response.headers)


async def _assert_audited(binding: Binding, action: str, code: str | None, bound: bool, address: str) -> None:
    """One row, with the reason, and the ids the response leaves out: the
    site the token names and the site the address leads to."""
    rows = [row for row in await binding.audit_rows() if row.action == action]
    assert [(row.result, row.reason_code) for row in rows] == [("allowed" if code is None else "refused", code)]
    assert rows[0].refs["site_bound"] is bound
    assert rows[0].refs.get("bound_site_id") == (str(binding.y.id) if bound else None)
    at_address = {"y": binding.y, "x": binding.x}.get(address)
    assert rows[0].refs.get("site_id") == (str(at_address.id) if at_address is not None else None)


@pytest.mark.parametrize(("bound", "address", "links", "required"), GRID, ids=[_cell_id(cell) for cell in GRID])
async def test_a_deploy_follows_the_decision_table(
    binding: Binding, bound: bool, address: str, links: str, required: bool
) -> None:
    await _set_up_cell(binding, links, required)
    async with binding.client() as client:
        response = await client.post(
            f"/-/api/v1/sites/{GROUP}/{SLUGS[address]}/deploys", files=_upload(), headers=_bearer(binding.token(bound))
        )

    status, code = _expected(bound, address, links, required)
    target = {"y": binding.y, "x": binding.x}.get(address)
    if code is None:
        assert response.status_code == 201
        assert await binding.versions_per_site() == {target.id: 1}
    else:
        assert response.status_code == status
        assert response.json()["code"] == code
        assert await binding.versions_per_site() == {}
    if code == "SITE_MOVED":
        assert f"{GROUP}/{SLUGS['y']}" in response.json()["detail"]
    if status == 401:
        assert response.headers["www-authenticate"].startswith("Bearer")
    await _assert_no_site_id_in(binding, response)
    await _assert_audited(binding, "deploy", code, bound, address)


@pytest.mark.parametrize(("bound", "address", "links", "required"), GRID, ids=[_cell_id(cell) for cell in GRID])
async def test_a_preview_teardown_follows_the_decision_table(
    binding: Binding, bound: bool, address: str, links: str, required: bool
) -> None:
    await _set_up_cell(binding, links, required)
    await binding.add_preview(binding.y)
    await binding.add_preview(binding.x)
    async with binding.client() as client:
        response = await client.delete(
            f"/-/api/v1/sites/{GROUP}/{SLUGS[address]}/previews/{PREVIEW_REF}", headers=_bearer(binding.token(bound))
        )

    status, code = _expected(bound, address, links, required)
    target = {"y": binding.y, "x": binding.x}.get(address)
    if code is None:
        assert response.status_code == 204
        assert await binding.previews() == {binding.y.id, binding.x.id} - {target.id}
    else:
        assert response.status_code == status
        assert response.json()["code"] == code
        assert await binding.previews() == {binding.y.id, binding.x.id}
    if code == "SITE_MOVED":
        assert f"{GROUP}/{SLUGS['y']}" in response.json()["detail"]
    await _assert_no_site_id_in(binding, response)
    await _assert_audited(binding, "preview_teardown", code, bound, address)


# -- The cases the table is for ----------------------------------------------


async def test_a_bound_workflow_after_a_rename_is_told_the_new_address(binding: Binding) -> None:
    await binding.link(binding.y, REPOSITORY, required=True)
    async with binding.factory() as db:
        await db.execute(update(Site).where(Site.id == binding.y.id).values(slug="nieuwe-website"))
        await db.commit()
    async with binding.client() as client:
        response = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(binding.token(True))
        )
    assert response.status_code == 409
    assert response.json()["code"] == "SITE_MOVED"
    assert f"{GROUP}/nieuwe-website" in response.json()["detail"]
    await _assert_no_site_id_in(binding, response)


async def test_a_link_that_took_a_token_without_site_id_requires_one_once_its_site_moves(binding: Binding) -> None:
    """The exemption of a link from before the site id ends with the move
    (spec 6.4), and the API follows no old address."""
    await binding.link(binding.y, REPOSITORY, required=False)
    async with binding.factory() as db:
        (await db.get(Site, binding.y.id)).slug = "nieuwe-website"
        await db.commit()

    async with binding.client() as client:
        at_the_new = await client.post(
            f"/-/api/v1/sites/{GROUP}/nieuwe-website/deploys", files=_upload(), headers=_bearer(binding.token(False))
        )
        at_the_old = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(binding.token(False))
        )

    assert at_the_new.status_code == 403
    assert at_the_new.json()["code"] == "CI_SITE_ID_REQUIRED"
    assert at_the_old.status_code == 404
    assert at_the_old.json()["code"] == "UNKNOWN_SITE"
    assert await binding.versions_per_site() == {}


async def test_a_bound_workflow_follows_its_site_when_the_group_moves(binding: Binding) -> None:
    await binding.link(binding.y, REPOSITORY, required=False)
    async with binding.factory() as db:
        (await db.get(Group, binding.group.id)).slug = "ploeg-aurora"
        await db.commit()

    async with binding.client() as client:
        unbound = await client.post(
            "/-/api/v1/sites/ploeg-aurora/website/deploys", files=_upload(), headers=_bearer(binding.token(False))
        )
        old = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(binding.token(True))
        )
        new = await client.post(
            "/-/api/v1/sites/ploeg-aurora/website/deploys", files=_upload(), headers=_bearer(binding.token(True))
        )

    assert unbound.status_code == 403
    assert unbound.json()["code"] == "CI_SITE_ID_REQUIRED"
    assert old.status_code == 409
    assert old.json()["code"] == "SITE_MOVED"
    assert "ploeg-aurora/website" in old.json()["detail"]
    assert new.status_code == 201
    assert await binding.versions_per_site() == {binding.y.id: 1}
    for response in (unbound, old, new):
        await _assert_no_site_id_in(binding, response)


async def test_a_claimant_of_the_old_address_with_the_same_repository_never_gets_the_build(
    binding: Binding,
) -> None:
    """Y moves; once its old address is free again, someone creates a site
    there and links the very repository that publishes to Y. The bound
    workflow, still on the old `site:`, is pointed at Y's new address; the
    one without a site id meets a link that requires one. Neither lands on
    the claimant's site."""
    await binding.link(binding.y, REPOSITORY, required=False)
    async with binding.factory() as db:
        await db.execute(update(Site).where(Site.id == binding.y.id).values(slug="verhuisd"))
        # What the nightly cleanup does once the old address stopped redirecting.
        await db.execute(delete(SiteSlug).where(SiteSlug.site_id == binding.y.id, SiteSlug.slug == "website"))
        claimant = Site(group_id=binding.group.id, slug="website", title="Overgenomen", access_base=AccessBase.PUBLIC)
        db.add(claimant)
        await db.commit()
    await binding.link(claimant, REPOSITORY, required=True)

    async with binding.client() as client:
        bound = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(binding.token(True))
        )
        unbound = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(binding.token(False))
        )

    assert bound.status_code == 409
    assert bound.json()["code"] == "SITE_MOVED"
    assert f"{GROUP}/verhuisd" in bound.json()["detail"]
    assert unbound.status_code == 403
    assert unbound.json()["code"] == "CI_SITE_ID_REQUIRED"
    assert await binding.versions_per_site() == {}
    for response in (bound, unbound):
        await _assert_no_site_id_in(binding, response)


async def test_a_token_bound_to_a_site_that_no_longer_exists_is_401(binding: Binding) -> None:
    await binding.link(binding.y, REPOSITORY, required=False)
    token = binding.token(True, aud=f"{AUDIENCE}/-/sites/{uuid.uuid4()}")
    async with binding.client() as client:
        response = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(token)
        )
    assert response.status_code == 401
    assert response.json()["code"] == "CI_AUDIENCE_MISMATCH"
    assert await binding.versions_per_site() == {}
    assert [(row.result, row.reason_code) for row in await binding.audit_rows()] == [
        ("refused", "CI_AUDIENCE_MISMATCH")
    ]


async def test_a_stranger_bound_to_a_site_gets_the_same_answer_at_every_address(binding: Binding) -> None:
    """Whoever holds a site id but not the repository linked to that site
    cannot use the token to find out which address is that site."""
    await binding.link(binding.y, OTHER_REPOSITORY, required=True)
    await binding.link(binding.x, REPOSITORY, required=False)
    token = binding.token(True)
    async with binding.client() as client:
        answers = [
            await client.post(f"/-/api/v1/sites/{GROUP}/{slug}/deploys", files=_upload(), headers=_bearer(token))
            for slug in SLUGS.values()
        ]
    assert {answer.status_code for answer in answers} == {401}
    assert len({answer.content for answer in answers}) == 1
    assert answers[0].json()["code"] == "CI_AUDIENCE_MISMATCH"
    assert answers[0].json()["detail"] == "This ID token's repository is not linked to the site its site id names."
    assert await binding.versions_per_site() == {}


async def test_a_bound_token_to_an_unlinked_site_learns_no_address(binding: Binding) -> None:
    await binding.link(binding.x, REPOSITORY, required=False)
    async with binding.client() as client:
        response = await client.post(
            f"/-/api/v1/sites/{GROUP}/docs/deploys", files=_upload(), headers=_bearer(binding.token(True))
        )
    assert response.status_code == 401
    assert response.json()["code"] == "CI_AUDIENCE_MISMATCH"
    assert SLUGS["y"] not in response.text
    assert await binding.versions_per_site() == {}


@pytest.mark.parametrize(
    "aud",
    [
        "{base}/-/sites/{site_upper}",
        "{base}/-/sites/{site}/",
        "{base}//-/sites/{site}",
    ],
)
async def test_an_audience_that_is_almost_the_bound_one_is_refused(binding: Binding, aud: str) -> None:
    await binding.link(binding.y, REPOSITORY, required=False)
    token = binding.token(
        True, aud=aud.format(base=AUDIENCE, site=binding.y.id, site_upper=str(binding.y.id).upper())
    )
    async with binding.client() as client:
        response = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(token)
        )
    assert response.status_code == 401
    assert response.json()["code"] == "CI_AUDIENCE_MISMATCH"
    assert await binding.versions_per_site() == {}


async def test_a_bound_token_still_goes_live_only_from_a_live_event(binding: Binding) -> None:
    await binding.link(binding.y, REPOSITORY, required=True)
    async with binding.client() as client:
        response = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys",
            files=_upload(),
            headers=_bearer(binding.token(True, event_name="pull_request")),
        )
    assert response.status_code == 403
    assert response.json()["code"] == "CI_BRANCH_NOT_ALLOWED"


async def test_a_forgejo_token_without_ids_is_checked_against_forgejo_before_the_address_is_named(
    binding: Binding,
) -> None:
    await binding.link(binding.y, REPOSITORY, required=True, provider=CiProvider.FORGEJO)
    token = binding.ci.token("forgejo", aud=f"{AUDIENCE}/-/sites/{binding.y.id}")
    async with binding.client() as client:
        response = await client.post(f"/-/api/v1/sites/{GROUP}/docs/deploys", files=_upload(), headers=_bearer(token))
    assert response.status_code == 409
    assert response.json()["code"] == "SITE_MOVED"
    assert FORGEJO_HOST + "/api/v1/repos/minbzk/website" in binding.ci.requests


async def test_an_unreachable_forgejo_leaves_the_question_open_and_names_nothing(binding: Binding) -> None:
    await binding.link(binding.y, REPOSITORY, required=True, provider=CiProvider.FORGEJO)
    binding.ci.failures[FORGEJO_HOST + "/api/v1/repos/minbzk/website"] = 502
    token = binding.ci.token("forgejo", aud=f"{AUDIENCE}/-/sites/{binding.y.id}")
    async with binding.client() as client:
        response = await client.post(f"/-/api/v1/sites/{GROUP}/docs/deploys", files=_upload(), headers=_bearer(token))
    assert response.status_code == 503
    assert response.json()["code"] == "CI_PROVIDER_UNREACHABLE"
    assert SLUGS["y"] not in response.json()["detail"]
    await _assert_no_site_id_in(binding, response)


async def test_a_cli_token_is_not_asked_for_a_site_id(binding: Binding) -> None:
    """The site id is between a workflow and a site; a member's own CLI token
    acts on its role, wherever the link stands."""
    await binding.link(binding.y, REPOSITORY, required=True)
    token = await _cli_login(binding.factory, binding.member)
    async with binding.client() as client:
        response = await client.post(
            f"/-/api/v1/sites/{GROUP}/website/deploys", files=_upload(), headers=_bearer(token)
        )
    assert response.status_code == 201
    async with binding.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Version).where(Version.site_id == binding.y.id)) == 1
    [row] = await binding.audit_rows()
    assert "site_id" not in row.refs
    assert "bound_site_id" not in row.refs
