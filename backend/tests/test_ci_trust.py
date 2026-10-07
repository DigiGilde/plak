"""Tests for ci/trust.py: trusted_repository against a site_repositories row,
check_live_deploy, audit_refs, actor identifiers. Uses the migrated test
database (migrated_dsn); container-free tests live in test_ci_tokens.py and
test_ci_providers.py."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from helpers_ci import FORGEJO_HOST, FORGEJO_ISSUER, MockCi
from sqlalchemy import update

from plak.audit import vocabulary
from plak.ci.providers import GITHUB_HOST, GITHUB_ISSUER, Issuer, ProviderClient
from plak.ci.tokens import CiTokenError, VerifiedCiToken
from plak.ci.trust import (
    AUDIT_CLAIMS,
    audit_refs,
    check_live_deploy,
    ci_actor_identifier,
    follow_token,
    refused_actor_identifier,
    trusted_repository,
)
from plak.constants import AccessBase
from plak.db import make_engine, make_session_factory
from plak.models.ci import CiProvider, SiteRepository
from plak.models.identity import Group
from plak.models.publication import Site

DB_URL = "postgresql+asyncpg://plak:plak@localhost:5432/plak"
GITHUB_ISSUER_OBJ = Issuer(CiProvider.GITHUB, GITHUB_HOST, GITHUB_ISSUER)
FORGEJO_ISSUER_OBJ = Issuer(CiProvider.FORGEJO, FORGEJO_HOST, FORGEJO_ISSUER)


def _token(issuer: Issuer, **claims: object) -> VerifiedCiToken:
    return VerifiedCiToken(issuer=issuer, claims=claims)


@pytest_asyncio.fixture
async def factory(migrated_dsn: str):
    from plak.config import Settings

    settings = Settings(
        db_url=migrated_dsn,
        content_root="/onbestaand/plak-content",
        oidc_issuer="https://idp.example",
        oidc_client_id="plak",
        oidc_client_private_jwk="{}",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://plak.example",
        environment="dev",
    )
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def _make_site(factory, *, slug: str = "website") -> Site:
    group = Group(
        id=uuid.uuid4(), slug=f"groep-{uuid.uuid4().hex[:8]}", name="Groep", default_access_base=AccessBase.PUBLIC
    )
    site = Site(id=uuid.uuid4(), group_id=group.id, slug=slug, title="Website", access_base=AccessBase.PUBLIC)
    async with factory() as db:
        db.add_all([group, site])
        await db.commit()
    return site


async def _add_repository(
    factory,
    site: Site,
    *,
    provider: CiProvider = CiProvider.GITHUB,
    host: str = GITHUB_HOST,
    owner: str = "minbzk",
    repo: str = "website",
    repository_id: int = 1001,
    owner_id: int = 2002,
    live_branch: str | None = None,
    ids_confirmed: bool = False,
    # False by default: most tests here are about matching the repository,
    # with a token whose audience is the instance, as a link from before
    # migration 0004 accepts.
    site_id_required: bool = False,
) -> SiteRepository:
    row = SiteRepository(
        id=uuid.uuid4(),
        site_id=site.id,
        provider=provider,
        host=host,
        owner=owner,
        repo=repo,
        repository_id=repository_id,
        owner_id=owner_id,
        live_branch=live_branch,
        ids_confirmed=ids_confirmed,
        site_id_required=site_id_required,
    )
    async with factory() as db:
        db.add(row)
        await db.commit()
    return row


class TestTrustedRepositoryIdPath:
    async def test_no_row_refused(self, factory):
        site = await _make_site(factory)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="1001")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED
        assert exc.value.status == 403

    async def test_provider_mismatch_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, provider=CiProvider.GITHUB, host=GITHUB_HOST)
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_host_mismatch_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST)
        other_forgejo = Issuer(CiProvider.FORGEJO, "https://forgejo.example", "https://forgejo.example/api/actions")
        token = _token(other_forgejo, repository="minbzk/website")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_id_match_accepted(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, repository_id=1001, owner_id=2002)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="1001", repository_owner_id="2002")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert trusted.repository_id == 1001
        assert trusted.owner_id == 2002
        assert trusted.confirms_ids is True

    async def test_id_mismatch_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, repository_id=1001, owner_id=2002)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="9999")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_owner_id_mismatch_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, repository_id=1001, owner_id=2002)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="1001", repository_owner_id="9999")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_owner_id_absent_accepted(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, repository_id=1001, owner_id=2002)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="1001")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert trusted.repository_id == 1001
        # Nothing vouched for the owner id, so the link stays unconfirmed.
        assert trusted.confirms_ids is False

    async def test_github_without_ids_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, repository_id=1001, owner_id=2002)
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED


class TestSiteBinding:
    """A token bound to a site matches only that site; a token without a
    site id matches only a link that still accepts one, and is refused only
    once the repository itself matched."""

    async def test_a_token_without_a_site_id_is_refused_where_the_link_requires_one(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, site_id_required=True)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="1001", repository_owner_id="2002")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_SITE_ID_REQUIRED
        assert exc.value.status == 403
        assert str(site.id) not in str(exc.value)

    async def test_a_token_without_a_site_id_is_trusted_where_the_link_still_accepts_one(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, site_id_required=False)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="1001", repository_owner_id="2002")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert trusted.repository_id == 1001

    @pytest.mark.parametrize("required", [True, False])
    async def test_a_token_bound_to_this_site_is_trusted_either_way(self, factory, required):
        site = await _make_site(factory)
        await _add_repository(factory, site, site_id_required=required)
        token = VerifiedCiToken(
            issuer=GITHUB_ISSUER_OBJ, claims={"repository_id": "1001"}, bound_site_id=site.id
        )
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert trusted.repository_id == 1001

    async def test_a_token_bound_to_another_site_never_matches_this_one(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, site_id_required=False)
        token = VerifiedCiToken(
            issuer=GITHUB_ISSUER_OBJ, claims={"repository_id": "1001"}, bound_site_id=uuid.uuid4()
        )
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_AUDIENCE_MISMATCH
        assert exc.value.status == 401

    async def test_another_repository_is_refused_as_such_before_the_site_id_is_asked_for(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, site_id_required=True)
        token = _token(GITHUB_ISSUER_OBJ, repository_id="9999")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_a_forgejo_name_token_is_rechecked_before_the_site_id_is_asked_for(self, factory):
        """A repository recreated under the linked name is another repository,
        not a workflow that forgot its site id."""
        site = await _make_site(factory)
        await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, site_id_required=True
        )
        ci = MockCi()
        ci.add_forgejo("minbzk", "website", 9999, 2002)
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(ci.client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED


class TestTrustedRepositoryName:
    """The name on the trusted repository, and so a version's origin, comes
    from the signed `repository` claim, not from the stored link."""

    async def test_claim_wins_over_a_differently_typed_stored_name(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, owner="minbzk", repo="website", repository_id=1001, owner_id=2002)
        token = _token(GITHUB_ISSUER_OBJ, repository="eigen-org/eigen-repo", repository_id="1001")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert trusted.origin == "github.com/eigen-org/eigen-repo"

    async def test_renamed_repository_shows_its_new_name(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, owner="minbzk", repo="oude-naam", repository_id=1001, owner_id=2002)
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/nieuwe-naam", repository_id="1001")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert trusted.origin == "github.com/minbzk/nieuwe-naam"

    @pytest.mark.parametrize(
        "claim",
        [
            None,
            "",
            "minbzk",
            "minbzk/website/extra",
            "/website",
            "minbzk/",
            "minbzk/web site",
            "../website",
            "minbzk/" + "a" * 101,
            1001,
        ],
    )
    async def test_missing_or_malformed_claim_falls_back_to_the_stored_name(self, factory, claim):
        site = await _make_site(factory)
        await _add_repository(factory, site, owner="minbzk", repo="website", repository_id=1001, owner_id=2002)
        claims: dict[str, object] = {"repository_id": "1001"}
        if claim is not None:
            claims["repository"] = claim
        token = _token(GITHUB_ISSUER_OBJ, **claims)
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        assert trusted.origin == "github.com/minbzk/website"


class TestFollowToken:
    async def test_stores_the_token_name_and_returns_the_previous_one(self, factory):
        site = await _make_site(factory)
        row = await _add_repository(factory, site, owner="minbzk", repo="oude-naam")
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/nieuwe-naam", repository_id="1001")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
            previous = await follow_token(db, site, trusted)
        assert previous == "minbzk/oude-naam"
        async with factory() as db:
            stored = await db.get(SiteRepository, row.id)
        assert (stored.owner, stored.repo) == ("minbzk", "nieuwe-naam")

    async def test_same_name_changes_nothing(self, factory):
        site = await _make_site(factory)
        await _add_repository(factory, site, owner="minbzk", repo="website")
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/website", repository_id="1001")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
            assert await follow_token(db, site, trusted) is None

    @pytest.mark.parametrize(
        ("claims", "confirmed_before", "confirmed_after"),
        [
            ({"repository_id": "1001", "repository_owner_id": "2002"}, False, True),
            ({"repository_id": "1001"}, False, False),
            ({"repository_id": "1001", "repository_owner_id": "2002"}, True, True),
            ({"repository_id": "1001"}, True, True),
        ],
    )
    async def test_a_token_that_vouches_for_both_ids_confirms_them(
        self, factory, claims, confirmed_before, confirmed_after
    ):
        site = await _make_site(factory)
        row = await _add_repository(factory, site, ids_confirmed=confirmed_before)
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/website", **claims)
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
            assert await follow_token(db, site, trusted) is None
        async with factory() as db:
            stored = await db.get(SiteRepository, row.id)
        assert stored.ids_confirmed is confirmed_after

    async def test_a_forgejo_name_token_confirms_the_ids_after_the_rest_check(self, factory):
        site = await _make_site(factory)
        row = await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, repository_id=1001, owner_id=2002
        )
        ci = MockCi()
        ci.add_forgejo("minbzk", "website", 1001, 2002)
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(ci.client()))
            await follow_token(db, site, trusted)
        async with factory() as db:
            stored = await db.get(SiteRepository, row.id)
        assert stored.ids_confirmed is True

    @pytest.mark.parametrize("replaced", [{"repository_id": 5005}, {"owner_id": 6006}])
    async def test_a_link_with_other_ids_is_not_confirmed(self, factory, replaced):
        site = await _make_site(factory)
        row = await _add_repository(factory, site)
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/website", repository_id="1001", repository_owner_id="2002")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        async with factory() as db:
            await db.execute(update(SiteRepository).where(SiteRepository.id == row.id).values(**replaced))
            await db.commit()
        async with factory() as db:
            await follow_token(db, site, trusted)
            stored = await db.get(SiteRepository, row.id)
        assert stored.ids_confirmed is False

    async def test_a_link_replaced_in_the_meantime_is_left_alone(self, factory):
        site = await _make_site(factory)
        row = await _add_repository(factory, site, owner="minbzk", repo="oude-naam", repository_id=1001)
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/nieuwe-naam", repository_id="1001")
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(MockCi().client()))
        # An admin links another repository before the rename is stored.
        async with factory() as db:
            stored = await db.get(SiteRepository, row.id)
            stored.owner, stored.repo, stored.repository_id = "ander", "project", 5005
            await db.commit()
        async with factory() as db:
            assert await follow_token(db, site, trusted) is None
            stored = await db.get(SiteRepository, row.id)
        assert (stored.owner, stored.repo, stored.repository_id) == ("ander", "project", 5005)


class TestTrustedRepositoryForgejoNamePath:
    async def test_name_match_accepted_with_rest_confirmation(self, factory):
        site = await _make_site(factory)
        await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, owner="minbzk", repo="website",
            repository_id=1001, owner_id=2002,
        )
        ci = MockCi()
        ci.add_forgejo("minbzk", "website", 1001, 2002)
        token = _token(FORGEJO_ISSUER_OBJ, repository="MinBZK/Website")  # case-insensitive
        async with factory() as db:
            trusted = await trusted_repository(db, token, site, ProviderClient(ci.client()))
        assert trusted.repository_id == 1001
        assert trusted.confirms_ids is True
        # The name is the token's spelling, not the stored one.
        assert trusted.origin == "code.overheid.nl/MinBZK/Website"

    async def test_name_mismatch_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, owner="minbzk", repo="website",
            repository_id=1001, owner_id=2002,
        )
        ci = MockCi()
        ci.add_forgejo("minbzk", "website", 1001, 2002)
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/andere-repo")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(ci.client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_rest_says_different_id_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, owner="minbzk", repo="website",
            repository_id=1001, owner_id=2002,
        )
        ci = MockCi()
        # Recreated under the same name: the REST answer no longer has the stored id.
        ci.add_forgejo("minbzk", "website", 9999, 2002)
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(ci.client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_rest_404_refused(self, factory):
        site = await _make_site(factory)
        await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, owner="minbzk", repo="website",
            repository_id=1001, owner_id=2002,
        )
        ci = MockCi()  # repository not registered: REST answers 404
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(ci.client()))
        assert exc.value.reason == vocabulary.CI_REPOSITORY_NOT_TRUSTED

    async def test_rest_unreachable_returns_503(self, factory):
        site = await _make_site(factory)
        await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, owner="minbzk", repo="website",
            repository_id=1001, owner_id=2002,
        )
        ci = MockCi()
        ci.add_forgejo("minbzk", "website", 1001, 2002)
        url = FORGEJO_HOST + "/api/v1/repos/minbzk/website"
        ci.failures[url] = 500
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            with pytest.raises(CiTokenError) as exc:
                await trusted_repository(db, token, site, ProviderClient(ci.client()))
        assert exc.value.reason == vocabulary.CI_PROVIDER_UNREACHABLE
        assert exc.value.status == 503

    async def test_second_call_within_5min_cached(self, factory):
        site = await _make_site(factory)
        await _add_repository(
            factory, site, provider=CiProvider.FORGEJO, host=FORGEJO_HOST, owner="minbzk", repo="website",
            repository_id=1001, owner_id=2002,
        )
        ci = MockCi()
        ci.add_forgejo("minbzk", "website", 1001, 2002)
        providers = ProviderClient(ci.client())
        token = _token(FORGEJO_ISSUER_OBJ, repository="minbzk/website")
        async with factory() as db:
            await trusted_repository(db, token, site, providers)
        request_count = len(ci.requests)
        async with factory() as db:
            await trusted_repository(db, token, site, providers)
        assert len(ci.requests) == request_count  # served from the 5-minute positive cache


class TestCheckLiveDeploy:
    def _repository(self, live_branch):
        from plak.ci.trust import TrustedRepository

        return TrustedRepository(
            provider=CiProvider.GITHUB,
            host=GITHUB_HOST,
            owner="minbzk",
            repo="website",
            repository_id=1001,
            owner_id=2002,
            live_branch=live_branch,
        )

    # (live_branch, event_name, ref, allowed); event None leaves the claim out.
    CASES = (
        *[(None, event, "refs/heads/feature", True) for event in ("push", "workflow_dispatch", "schedule")],
        (None, "push", "refs/tags/v1", True),
        *[("main", event, "refs/heads/main", True) for event in ("push", "workflow_dispatch", "schedule")],
        *[
            (branch, event, ref, False)
            for branch in (None, "main")
            for ref in ("refs/heads/main", "refs/pull/7/merge")
            for event in (
                "pull_request",
                "pull_request_target",
                "pull_request_review",
                "issue_comment",
                "workflow_run",
                "discussion",
                "release",
                "onbekend",
                "",
                None,
            )
        ],
        ("main", "push", "refs/heads/feature", False),
        ("main", "push", "refs/tags/v1", False),
        ("main", "workflow_dispatch", "refs/heads/mainx", False),
        ("main", "schedule", None, False),
    )

    @pytest.mark.parametrize(("live_branch", "event", "ref", "allowed"), CASES)
    def test_live_deploy_policy(self, live_branch, event, ref, allowed):
        claims = {}
        if event is not None:
            claims["event_name"] = event
        if ref is not None:
            claims["ref"] = ref
        token = _token(GITHUB_ISSUER_OBJ, **claims)
        if allowed:
            check_live_deploy(self._repository(live_branch), token)
            return
        with pytest.raises(CiTokenError) as exc:
            check_live_deploy(self._repository(live_branch), token)
        assert exc.value.reason == vocabulary.CI_BRANCH_NOT_ALLOWED
        assert exc.value.status == 403


class TestAuditAndIdentifiers:
    def test_audit_refs_caps_length_and_stringifies_ints(self):
        token = _token(
            GITHUB_ISSUER_OBJ,
            repository="minbzk/website",
            ref="refs/heads/main",
            sha="a" * 300,
            run_id=4242,
            workflow="Publiceer",
            event_name="push",
        )
        refs = audit_refs(token)
        assert refs["provider"] == "github"
        assert refs["sha"] == "a" * 200
        assert refs["run_id"] == "4242"
        assert set(refs) <= {"provider", "site_bound", "bound_site_id", *AUDIT_CLAIMS}

    @pytest.mark.parametrize(("bound_site_id", "site_bound"), [(None, False), (uuid.uuid4(), True)])
    def test_audit_refs_say_whether_the_token_named_a_site_and_which(self, bound_site_id, site_bound):
        """The id goes into the audit row; it never goes into a response,
        which test_ci_binding.py holds every answer to."""
        token = VerifiedCiToken(issuer=GITHUB_ISSUER_OBJ, claims={}, bound_site_id=bound_site_id)
        refs = audit_refs(token)
        assert refs["site_bound"] is site_bound
        assert refs.get("bound_site_id") == (str(bound_site_id) if site_bound else None)

    def test_audit_refs_skips_absent_claims(self):
        token = _token(GITHUB_ISSUER_OBJ, repository="minbzk/website")
        refs = audit_refs(token)
        assert "ref" not in refs
        assert refs["repository"] == "minbzk/website"

    def test_refused_actor_identifier_with_repository_id(self):
        token = _token(GITHUB_ISSUER_OBJ, repository_id="1001")
        assert refused_actor_identifier(token) == ci_actor_identifier(CiProvider.GITHUB, GITHUB_HOST, "1001")

    def test_refused_actor_identifier_falls_back_to_lowercased_name(self):
        token = _token(FORGEJO_ISSUER_OBJ, repository="MinBZK/Website")
        assert refused_actor_identifier(token) == ci_actor_identifier(
            CiProvider.FORGEJO, FORGEJO_HOST, "minbzk/website"
        )

    def test_refused_actor_identifier_with_neither_claim(self):
        token = _token(GITHUB_ISSUER_OBJ)
        assert refused_actor_identifier(token) == ci_actor_identifier(CiProvider.GITHUB, GITHUB_HOST, "")

    def test_trusted_repository_origin_github(self):
        from plak.ci.trust import TrustedRepository

        repository = TrustedRepository(
            provider=CiProvider.GITHUB, host=GITHUB_HOST, owner="minbzk", repo="website",
            repository_id=1001, owner_id=2002, live_branch=None,
        )
        assert repository.origin == "github.com/minbzk/website"
        assert repository.actor_identifier == ci_actor_identifier(CiProvider.GITHUB, GITHUB_HOST, 1001)

    def test_trusted_repository_origin_forgejo(self):
        from plak.ci.trust import TrustedRepository

        repository = TrustedRepository(
            provider=CiProvider.FORGEJO, host=FORGEJO_HOST, owner="robbertbos", repo="waggle",
            repository_id=1, owner_id=2, live_branch=None,
        )
        assert repository.origin == "code.overheid.nl/robbertbos/waggle"
