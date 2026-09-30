"""Tests for the access gate (spec §7, §5.6): the full matrix of access policy
x visitor state for live content, previews (inherited, override, expired,
unknown) and _version views, plus edge cases, against the real PostgreSQL test
container.

The policy axis is every base times every combination of the two extras, so
the table covers the combinations the old single enum could not express: a
secret link beside a public base, invitees beside the site team, and the
closed base that only an extra opens."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from plak.access import keys
from plak.access.decision import DecisionKind
from plak.access.gate import Visitor, _is_invitee, code_page_needed, decide, decide_preview, decide_version
from plak.constants import AccessBase, AccessPolicy, Role
from plak.models.identity import Group, GroupMember, Member, MemberStatus, SiteMember
from plak.models.publication import AccessKey, Invitee, KeyStatus, Preview, Site, Version, VersionTarget


@pytest_asyncio.fixture
async def db(migrated_dsn: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@dataclass
class World:
    group_slug: str
    site_slug: str
    group_id: uuid.UUID
    site_id: uuid.UUID
    live_version_id: uuid.UUID
    preview_ref: str
    preview_version_id: uuid.UUID
    key_id: uuid.UUID
    key_plain: str
    revoked_key_id: uuid.UUID
    expired_key_plain: str
    other_site_key_plain: str
    other_site_id: uuid.UUID
    other_site_version_id: uuid.UUID


POLICIES = [
    AccessPolicy(base, keys=keys_on, invitees=invitees_on)
    for base in AccessBase
    for keys_on in (False, True)
    for invitees_on in (False, True)
]


def policy_id(policy: AccessPolicy) -> str:
    extras = "".join(
        [
            "+sleutels" if policy.keys else "",
            "+genodigden" if policy.invitees else "",
        ]
    )
    return f"{policy.base.value}{extras}"


async def make_world(
    db: AsyncSession,
    policy: AccessPolicy,
    *,
    preview_override: AccessPolicy | None = None,
    with_live: bool = True,
    preview_expires_at: datetime | None = None,
) -> World:
    group = Group(slug="aurora", name="Aurora", default_access_base=AccessBase.PUBLIC)
    db.add(group)
    await db.flush()

    member_active = Member(sso_subject="lid-actief", email="lid-actief@example.org", status=MemberStatus.ACTIVE)
    member_deactivated = Member(
        sso_subject="lid-gedeactiveerd", email="lid-gedeactiveerd@example.org", status=MemberStatus.DEACTIVATED
    )
    member_outside = Member(sso_subject="lid-buiten", email="lid-buiten@example.org", status=MemberStatus.ACTIVE)
    db.add_all([member_active, member_deactivated, member_outside])
    await db.flush()
    # lid-buiten is an active platform member but has no group_members row.
    db.add_all(
        [
            GroupMember(group_id=group.id, member_id=member_active.id, role=Role.ADMIN),
            GroupMember(group_id=group.id, member_id=member_deactivated.id, role=Role.ADMIN),
        ]
    )

    site = Site(
        group_id=group.id,
        slug="site",
        title="Site",
        access_base=policy.base,
        access_keys=policy.keys,
        access_invitees=policy.invitees,
    )
    other_site = Site(
        group_id=group.id,
        slug="ander",
        title="Ander",
        access_base=AccessBase.NOBODY,
        access_keys=True,
    )
    db.add_all([site, other_site])
    await db.flush()

    live = Version(
        site_id=site.id, target=VersionTarget.LIVE, storage_ref="aurora/site/live", member_id=member_active.id
    )
    preview_version = Version(
        site_id=site.id, target=VersionTarget.PREVIEW, storage_ref="aurora/site/pv", member_id=member_active.id
    )
    other_live = Version(
        site_id=other_site.id, target=VersionTarget.LIVE, storage_ref="aurora/ander/live",
        member_id=member_active.id,
    )
    db.add_all([live, preview_version, other_live])
    await db.flush()
    if with_live:
        site.live_version_id = live.id
    other_site.live_version_id = other_live.id

    db.add(
        Preview(
            site_id=site.id,
            ref="pr-42",
            version_id=preview_version.id,
            access_base_override=preview_override.base if preview_override else None,
            access_keys_override=preview_override.keys if preview_override else None,
            access_invitees_override=preview_override.invitees if preview_override else None,
            expires_at=preview_expires_at,
        )
    )
    db.add_all(
        [
            Invitee(site_id=site.id, identifier="genodigde-sub"),
            Invitee(site_id=site.id, identifier="genodigde@example.org"),
        ]
    )

    key, key_plain = await keys.create_key(db, site.id, "valid")
    revoked, _ = await keys.create_key(db, site.id, "revoked")
    await keys.revoke(db, revoked.id)
    # create_key refuses a past expiry, so the already-expired key is built
    # directly here, bypassing the guarantee.
    expired_verifier = keys._chars(keys.VERIFIER_LENGTH)
    expired_key = AccessKey(
        site_id=site.id,
        label="expired",
        selector=keys._chars(keys.SELECTOR_LENGTH),
        verifier_hash=keys.hash_verifier(expired_verifier),
        status=KeyStatus.ACTIVE,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db.add(expired_key)
    await db.flush()
    expired_plain = f"{expired_key.selector}.{expired_verifier}"
    _, other_plain = await keys.create_key(db, other_site.id, "other-site")
    await db.flush()

    return World(
        group_slug=group.slug,
        site_slug=site.slug,
        group_id=group.id,
        site_id=site.id,
        live_version_id=live.id,
        preview_ref="pr-42",
        preview_version_id=preview_version.id,
        key_id=key.id,
        key_plain=key_plain,
        revoked_key_id=revoked.id,
        expired_key_plain=expired_plain,
        other_site_key_plain=other_plain,
        other_site_id=other_site.id,
        other_site_version_id=other_live.id,
    )


def visitors(world: World) -> dict[str, Visitor]:
    return {
        "anonymous": Visitor(),
        "logged_in_without_member": Visitor(sub="kijker", email="kijker@example.org", email_verified=True),
        "group_member_active": Visitor(sub="lid-actief", email="lid-actief@example.org", email_verified=True),
        "group_member_deactivated": Visitor(
            sub="lid-gedeactiveerd", email="lid-gedeactiveerd@example.org", email_verified=True
        ),
        "member_outside_group": Visitor(sub="lid-buiten", email="lid-buiten@example.org", email_verified=True),
        "invitee_sub": Visitor(sub="genodigde-sub"),
        # The identifier is stored lowercased (check constraint on invitees);
        # an SSO subject has to be matched the same way.
        "invitee_sub_mixed_case": Visitor(sub="Genodigde-Sub"),
        "invitee_email": Visitor(sub="andere-sub", email="Genodigde@Example.org", email_verified=True),
        "invitee_email_unverified": Visitor(
            sub="andere-sub", email="genodigde@example.org", email_verified=False
        ),
        "key_query": Visitor(key_query=world.key_plain),
        "key_query_wrong": Visitor(key_query=f"{world.key_plain.split('.')[0]}.{'A' * 32}"),
        "key_query_expired": Visitor(key_query=world.expired_key_plain),
        "key_query_other_site": Visitor(key_query=world.other_site_key_plain),
        "key_cookie": Visitor(key_cookie=str(world.key_id)),
        "key_cookie_revoked": Visitor(key_cookie=str(world.revoked_key_id)),
    }


VISITOR_NAMES = [
    "anonymous",
    "logged_in_without_member",
    "group_member_active",
    "group_member_deactivated",
    "member_outside_group",
    "invitee_sub",
    "invitee_sub_mixed_case",
    "invitee_email",
    "invitee_email_unverified",
    "key_query",
    "key_query_wrong",
    "key_query_expired",
    "key_query_other_site",
    "key_cookie",
    "key_cookie_revoked",
]

WITH_SESSION = {
    "logged_in_without_member",
    "group_member_active",
    "group_member_deactivated",
    "member_outside_group",
    "invitee_sub",
    "invitee_sub_mixed_case",
    "invitee_email",
    "invitee_email_unverified",
}
VALID_KEY = {"key_query", "key_cookie"}
INVALID_KEY = {
    "key_query_wrong",
    "key_query_expired",
    "key_query_other_site",
    "key_cookie_revoked",
}
INVITED = {"invitee_sub", "invitee_sub_mixed_case", "invitee_email"}

ALLOW = (DecisionKind.ALLOW, "OK")


def expected(policy: AccessPolicy, name: str, *, preview: bool) -> tuple[DecisionKind, str]:
    """Expected outcome per (effective policy, visitor state), written out as
    the product owner stated the rule rather than as the gate implements it:
    the base OR a valid secret link OR an invitee with a session.

    On previews an anonymous visitor who could have logged in gets a neutral
    404 instead of a login redirect. The serving layer turns every anonymous
    preview refusal, this one included, into the login redirect on a
    top-level navigation; test_serving.py covers that.
    """
    if policy.base is AccessBase.PUBLIC:
        return ALLOW
    if policy.keys and name in VALID_KEY:
        return ALLOW
    if name in WITH_SESSION:
        if policy.base is AccessBase.SSO:
            return ALLOW
        if policy.base is AccessBase.SITE_TEAM and name == "group_member_active":
            return ALLOW
        if policy.invitees and name in INVITED:
            return ALLOW
        return (DecisionKind.NEUTRAL_404, "NO_ACCESS")
    # Anonymous, and no secret link got them in.
    if policy.login_can_help:
        if preview:
            return (DecisionKind.NEUTRAL_404, "NO_ACCESS")
        return (DecisionKind.LOGIN_REDIRECT, "LOGIN_REQUIRED")
    if policy.keys and name in INVALID_KEY:
        return (DecisionKind.NEUTRAL_404, "KEY_INVALID")
    return (DecisionKind.NEUTRAL_404, "NO_ACCESS")


def check(decision, kind: DecisionKind, reason: str, expected_version_id, expected_access) -> None:
    assert decision.kind is kind, (decision.kind, decision.reason_code)
    assert decision.reason_code == reason
    if kind is DecisionKind.ALLOW:
        assert decision.version_id == expected_version_id
        assert decision.effective_access == expected_access
    else:
        assert decision.version_id is None


# --- Who counts as belonging to this site ---


async def test_a_site_role_holder_may_look_at_what_they_publish(db):
    """The design calls this the heaviest decision it makes (3.3): the value
    base named site_team means everyone with an active role HERE, not only the
    group members. Without it someone with a role on one site can deploy and
    then not look at what they deployed, not even through _version. That is a
    defect, not a strict setting."""
    world = await make_world(db, AccessPolicy(AccessBase.SITE_TEAM))
    outsider = Member(sso_subject="lid-siterol", email="siterol@example.org", status=MemberStatus.ACTIVE)
    db.add(outsider)
    await db.flush()
    db.add(SiteMember(site_id=world.site_id, member_id=outsider.id, role=Role.EDITOR))
    await db.flush()
    visitor = Visitor(sub="lid-siterol")

    assert (await decide(db, world.group_slug, world.site_slug, visitor)).kind is DecisionKind.ALLOW
    version = await decide_version(db, world.group_slug, world.site_slug, world.live_version_id, visitor)
    assert version.kind is DecisionKind.ALLOW


# --- What deactivating a member does and does not close ---


@pytest.mark.parametrize(
    ("policy", "expected_kind", "why"),
    [
        (
            AccessPolicy(AccessBase.SITE_TEAM),
            DecisionKind.NEUTRAL_404,
            "content for group members runs through membership, and that is what closes",
        ),
        (
            AccessPolicy(AccessBase.SSO),
            DecisionKind.ALLOW,
            "SSO Rijk is about being able to log in at all; deactivating revokes no government account",
        ),
        (
            AccessPolicy(AccessBase.PUBLIC),
            DecisionKind.ALLOW,
            "public is public, with or without an account",
        ),
    ],
)
async def test_what_deactivation_closes_and_what_it_leaves_open(
    db, policy, expected_kind, why
):
    """Pins the reach of deactivating a member, because the word suggests more
    than it does: it shuts the admin environment and group-member content, and
    leaves every grant that does not run through membership standing."""
    world = await make_world(db, policy)
    decision = await decide(db, world.group_slug, world.site_slug, visitors(world)["group_member_deactivated"])
    assert decision.kind is expected_kind, why


async def test_an_invitation_outlives_deactivating_the_member(db):
    """The invitee list is keyed on an address, and most invitees never were
    members at all, so deactivating one does not strike them off. Whether that
    is what an admin expects is a design question; this makes the answer
    visible instead of leaving it to be discovered."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, invitees=True))
    deactivated = visitors(world)["group_member_deactivated"]
    invited = Visitor(sub=deactivated.sub, email="genodigde@example.org", email_verified=True)
    assert (await decide(db, world.group_slug, world.site_slug, invited)).kind is DecisionKind.ALLOW


async def test_a_secret_link_outlives_deactivating_the_member(db):
    """The key is the credential, not the account."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, keys=True))
    deactivated = visitors(world)["group_member_deactivated"]
    with_key = Visitor(sub=deactivated.sub, key_query=world.key_plain)
    assert (await decide(db, world.group_slug, world.site_slug, with_key)).kind is DecisionKind.ALLOW


# --- The matrix: live content ---


@pytest.mark.parametrize("name", VISITOR_NAMES)
@pytest.mark.parametrize("policy", POLICIES, ids=policy_id)
async def test_matrix_live(db, policy, name):
    world = await make_world(db, policy)
    decision = await decide(db, world.group_slug, world.site_slug, visitors(world)[name])
    kind, reason = expected(policy, name, preview=False)
    check(decision, kind, reason, world.live_version_id, policy)


# --- The matrix: a preview inherits the site's access policy ---


@pytest.mark.parametrize("name", VISITOR_NAMES)
@pytest.mark.parametrize("policy", POLICIES, ids=policy_id)
async def test_matrix_preview_inherits(db, policy, name):
    world = await make_world(db, policy)
    decision = await decide_preview(
        db, world.group_slug, world.site_slug, world.preview_ref, visitors(world)[name]
    )
    kind, reason = expected(policy, name, preview=True)
    check(decision, kind, reason, world.preview_version_id, policy)


# --- The matrix: a preview override beats the site's own policy ---
# Pairs (site policy, override) in which the two differ, so that every
# outcome proves the override and not the site decides.

OVERRIDE_PAIRS = [
    (AccessPolicy(AccessBase.SITE_TEAM), AccessPolicy(AccessBase.PUBLIC)),
    (AccessPolicy(AccessBase.PUBLIC), AccessPolicy(AccessBase.NOBODY, keys=True)),
    (AccessPolicy(AccessBase.PUBLIC), AccessPolicy(AccessBase.SSO)),
    (AccessPolicy(AccessBase.PUBLIC), AccessPolicy(AccessBase.SITE_TEAM)),
    (AccessPolicy(AccessBase.NOBODY, keys=True), AccessPolicy(AccessBase.NOBODY, invitees=True)),
    # The override carries its extras too: the site lets anyone with the link
    # in, the preview keeps that road open but adds the site team to it.
    (
        AccessPolicy(AccessBase.NOBODY, keys=True),
        AccessPolicy(AccessBase.SITE_TEAM, keys=True),
    ),
    # And the other way round: the site is wide open on its extras, the
    # preview drops both of them.
    (
        AccessPolicy(AccessBase.NOBODY, keys=True, invitees=True),
        AccessPolicy(AccessBase.SITE_TEAM),
    ),
]


@pytest.mark.parametrize("name", VISITOR_NAMES)
@pytest.mark.parametrize(("site_policy", "override"), OVERRIDE_PAIRS, ids=repr)
async def test_matrix_preview_override(db, site_policy, override, name):
    world = await make_world(db, site_policy, preview_override=override)
    decision = await decide_preview(
        db, world.group_slug, world.site_slug, world.preview_ref, visitors(world)[name]
    )
    kind, reason = expected(override, name, preview=True)
    check(decision, kind, reason, world.preview_version_id, override)


# --- The matrix: _version for whoever belongs to the site ---


@pytest.mark.parametrize("name", VISITOR_NAMES)
@pytest.mark.parametrize(
    "policy",
    [
        AccessPolicy(AccessBase.PUBLIC),
        AccessPolicy(AccessBase.NOBODY, keys=True),
        AccessPolicy(AccessBase.NOBODY, invitees=True),
    ],
    ids=policy_id,
)
async def test_matrix_version(db, policy, name):
    world = await make_world(db, policy)
    decision = await decide_version(
        db, world.group_slug, world.site_slug, world.live_version_id, visitors(world)[name]
    )
    if name == "group_member_active":
        check(decision, DecisionKind.ALLOW, "OK", world.live_version_id, AccessPolicy(AccessBase.SITE_TEAM))
    else:
        # Anonymous too: neutral 404, never a login redirect (spec §7).
        check(decision, DecisionKind.NEUTRAL_404, "NO_ACCESS", None, None)


# --- Edge cases ---


async def test_unknown_group(db):
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC))
    watcher = visitors(world)["anonymous"]
    for decision in [
        await decide(db, "bestaat-niet", world.site_slug, watcher),
        await decide_preview(db, "bestaat-niet", world.site_slug, world.preview_ref, watcher),
        await decide_version(db, "bestaat-niet", world.site_slug, world.live_version_id, watcher),
    ]:
        check(decision, DecisionKind.NEUTRAL_404, "UNKNOWN_GROUP", None, None)


async def test_unknown_site(db):
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC))
    watcher = visitors(world)["group_member_active"]
    for decision in [
        await decide(db, world.group_slug, "bestaat-niet", watcher),
        await decide_preview(db, world.group_slug, "bestaat-niet", world.preview_ref, watcher),
        await decide_version(db, world.group_slug, "bestaat-niet", world.live_version_id, watcher),
    ]:
        check(decision, DecisionKind.NEUTRAL_404, "UNKNOWN_SITE", None, None)


@pytest.mark.parametrize("name", ["anonymous", "group_member_active", "key_query"])
async def test_no_live_version_is_neutral_404_even_when_public(db, name):
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC), with_live=False)
    decision = await decide(db, world.group_slug, world.site_slug, visitors(world)[name])
    check(decision, DecisionKind.NEUTRAL_404, "NO_LIVE_VERSION", None, None)


async def test_preview_unknown_ref(db):
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC))
    decision = await decide_preview(db, world.group_slug, world.site_slug, "pr-99", visitors(world)["anonymous"])
    check(decision, DecisionKind.NEUTRAL_404, "UNKNOWN_PREVIEW", None, None)


@pytest.mark.parametrize("name", ["anonymous", "group_member_active", "key_query"])
@pytest.mark.parametrize(
    "policy",
    [
        AccessPolicy(AccessBase.PUBLIC),
        AccessPolicy(AccessBase.NOBODY, keys=True),
        AccessPolicy(AccessBase.SITE_TEAM),
    ],
    ids=policy_id,
)
async def test_expired_preview_is_neutral_404_on_the_decision(db, policy, name):
    """The expiry check happens in the gate itself, also for visitors who would
    otherwise have access; the cleanup job is only the safety net."""
    world = await make_world(db, policy, preview_expires_at=datetime.now(UTC) - timedelta(minutes=1))
    decision = await decide_preview(
        db, world.group_slug, world.site_slug, world.preview_ref, visitors(world)[name]
    )
    check(decision, DecisionKind.NEUTRAL_404, "PREVIEW_EXPIRED", None, None)


async def test_preview_with_future_expiry_date_stays_accessible(db):
    world = await make_world(
        db, AccessPolicy(AccessBase.PUBLIC), preview_expires_at=datetime.now(UTC) + timedelta(days=1)
    )
    decision = await decide_preview(
        db, world.group_slug, world.site_slug, world.preview_ref, visitors(world)["anonymous"]
    )
    check(decision, DecisionKind.ALLOW, "OK", world.preview_version_id, AccessPolicy(AccessBase.PUBLIC))


async def test_revoking_breaks_an_existing_cookie_immediately(db):
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, keys=True))
    cookie_visitor = Visitor(key_cookie=str(world.key_id))
    decision = await decide(db, world.group_slug, world.site_slug, cookie_visitor)
    assert decision.kind is DecisionKind.ALLOW

    await keys.revoke(db, world.key_id)

    decision = await decide(db, world.group_slug, world.site_slug, cookie_visitor)
    check(decision, DecisionKind.NEUTRAL_404, "KEY_INVALID", None, None)


async def test_key_on_preview_with_key_override(db):
    """The site's key works on the preview too when the effective policy
    there has secret links on."""
    world = await make_world(
        db,
        AccessPolicy(AccessBase.SITE_TEAM),
        preview_override=AccessPolicy(AccessBase.NOBODY, keys=True),
    )
    decision = await decide_preview(
        db, world.group_slug, world.site_slug, world.preview_ref, Visitor(key_query=world.key_plain)
    )
    check(decision, DecisionKind.ALLOW, "OK", world.preview_version_id, AccessPolicy(AccessBase.NOBODY, keys=True))


async def test_valid_query_beats_invalid_cookie(db):
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, keys=True))
    decision = await decide(
        db,
        world.group_slug,
        world.site_slug,
        Visitor(key_query=world.key_plain, key_cookie=str(world.revoked_key_id)),
    )
    check(decision, DecisionKind.ALLOW, "OK", world.live_version_id, AccessPolicy(AccessBase.NOBODY, keys=True))


async def test_valid_cookie_beats_invalid_query(db):
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, keys=True))
    decision = await decide(
        db,
        world.group_slug,
        world.site_slug,
        Visitor(key_query="kapot", key_cookie=str(world.key_id)),
    )
    check(decision, DecisionKind.ALLOW, "OK", world.live_version_id, AccessPolicy(AccessBase.NOBODY, keys=True))


async def test_version_unknown_id(db):
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC))
    decision = await decide_version(
        db, world.group_slug, world.site_slug, uuid.uuid4(), visitors(world)["group_member_active"]
    )
    check(decision, DecisionKind.NEUTRAL_404, "UNKNOWN_VERSION", None, None)


async def test_version_of_other_site_refused(db):
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC))
    decision = await decide_version(
        db,
        world.group_slug,
        world.site_slug,
        world.other_site_version_id,
        visitors(world)["group_member_active"],
    )
    check(decision, DecisionKind.NEUTRAL_404, "UNKNOWN_VERSION", None, None)


async def test_version_accessible_also_without_live_version(db):
    """_version is an inspection function and stands apart from the live pointer."""
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC), with_live=False)
    decision = await decide_version(
        db, world.group_slug, world.site_slug, world.preview_version_id, visitors(world)["group_member_active"]
    )
    check(decision, DecisionKind.ALLOW, "OK", world.preview_version_id, AccessPolicy(AccessBase.SITE_TEAM))


async def test_login_redirect_carries_the_policy_no_version(db):
    world = await make_world(db, AccessPolicy(AccessBase.SSO))
    decision = await decide(db, world.group_slug, world.site_slug, Visitor())
    assert decision.kind is DecisionKind.LOGIN_REDIRECT
    assert decision.version_id is None
    assert decision.effective_access == AccessPolicy(AccessBase.SSO)


# --- What the combination adds that a single level could not express ---


async def test_a_secret_link_opens_a_site_that_is_otherwise_sso_only(db):
    """The point of the extras: a road beside the base, not instead of it.
    Under the old single level this site had to choose between the two."""
    world = await make_world(db, AccessPolicy(AccessBase.SSO, keys=True))
    with_key = Visitor(key_query=world.key_plain)
    check(
        await decide(db, world.group_slug, world.site_slug, with_key),
        DecisionKind.ALLOW,
        "OK",
        world.live_version_id,
        AccessPolicy(AccessBase.SSO, keys=True),
    )


async def test_the_base_still_works_next_to_a_secret_link(db):
    """The same site from the other side: the key is an addition, so turning
    it on may not cost the logged-in visitor their access."""
    world = await make_world(db, AccessPolicy(AccessBase.SSO, keys=True))
    decision = await decide(db, world.group_slug, world.site_slug, visitors(world)["logged_in_without_member"])
    assert decision.kind is DecisionKind.ALLOW


async def test_a_stale_key_cookie_does_not_shut_out_a_logged_in_visitor(db):
    """Refusing on the invalid key first would turn the OR into an AND: this
    visitor may look because of the base, whatever the cookie says."""
    world = await make_world(db, AccessPolicy(AccessBase.SSO, keys=True))
    visitor = Visitor(sub="kijker", key_cookie=str(world.revoked_key_id))
    assert (await decide(db, world.group_slug, world.site_slug, visitor)).kind is DecisionKind.ALLOW


async def test_extras_add_nothing_to_a_public_base(db):
    """The interface says so, and the gate has to mean it: on base publiek
    every visitor is allowed, whatever they hand in."""
    world = await make_world(db, AccessPolicy(AccessBase.PUBLIC, keys=True, invitees=True))
    for name in ("anonymous", "key_query_wrong", "member_outside_group"):
        decision = await decide(db, world.group_slug, world.site_slug, visitors(world)[name])
        assert decision.kind is DecisionKind.ALLOW, name


async def test_closed_base_with_only_secret_links_stays_a_404_for_the_anonymous(db):
    """Nothing to log in for, so no login redirect: a login page here would
    say that the site exists to anyone who guesses the URL."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, keys=True))
    decision = await decide(db, world.group_slug, world.site_slug, Visitor())
    check(decision, DecisionKind.NEUTRAL_404, "NO_ACCESS", None, None)


async def test_closed_base_with_invitees_sends_the_anonymous_to_the_login(db):
    """The counterpart: an invitee cannot be recognised before logging in, so
    the redirect is the only way they can ever get in."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, invitees=True))
    decision = await decide(db, world.group_slug, world.site_slug, Visitor())
    assert decision.kind is DecisionKind.LOGIN_REDIRECT


async def test_closed_base_without_extras_is_reachable_for_nobody(db):
    """Base niemand standaard and both extras off: the site exists, has a live
    version, and no visitor in the matrix gets in."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY))
    for name in VISITOR_NAMES:
        decision = await decide(db, world.group_slug, world.site_slug, visitors(world)[name])
        assert decision.kind is DecisionKind.NEUTRAL_404, name


async def test_a_key_handed_in_where_only_the_login_helps_still_redirects(db):
    """An invalid key on a base that has a logged-in road: the redirect wins,
    because being sent to the login is the outcome that can still help."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, keys=True, invitees=True))
    decision = await decide(db, world.group_slug, world.site_slug, Visitor(key_query="kapot"))
    assert decision.kind is DecisionKind.LOGIN_REDIRECT


async def test_the_code_page_follows_the_secret_link_extra_not_the_base(db):
    """A link shared without its code asks for the code on every site that has
    secret links on, also when the base already lets the site team in."""
    world = await make_world(db, AccessPolicy(AccessBase.SITE_TEAM, keys=True))
    selector = world.key_plain.split(".")[0]
    assert await code_page_needed(db, world.group_slug, world.site_slug, selector) is True

    site = await db.get(Site, world.site_id)
    site.access_keys = False
    await db.flush()
    assert await code_page_needed(db, world.group_slug, world.site_slug, selector) is False


async def test_code_page_needed_false_for_an_unknown_group_or_site(db):
    """The code page must not leak that a selector would otherwise be usable
    on a group or site that does not exist: False, same as any other refusal
    here, never an exception or a different signal."""
    world = await make_world(db, AccessPolicy(AccessBase.SITE_TEAM, keys=True))
    selector = world.key_plain.split(".")[0]
    assert await code_page_needed(db, "bestaat-niet", world.site_slug, selector) is False
    assert await code_page_needed(db, world.group_slug, "bestaat-niet", selector) is False


# --- _is_invitee as a unit: the identifier list it builds ---


async def test_is_invitee_matches_on_email_alone_without_a_session_subject(db):
    """_is_invitee builds its identifier list from whatever the visitor
    carries; a caller that already has a verified email but no `sub` (not the
    gate's own callers today, which all guard on `sub is not None`, but the
    function itself does not assume that) still gets a correct match."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, invitees=True))
    visitor = Visitor(sub=None, email="genodigde@example.org", email_verified=True)
    assert await _is_invitee(db, world.site_id, visitor) is True


async def test_is_invitee_false_when_nothing_identifies_the_visitor(db):
    """No sub and no verified email leaves the identifier list empty: nothing
    to look up, so the function refuses before ever touching the database."""
    world = await make_world(db, AccessPolicy(AccessBase.NOBODY, invitees=True))
    assert await _is_invitee(db, world.site_id, Visitor()) is False
    # An unverified email is the same as no email at all here.
    unverified = Visitor(email="genodigde@example.org", email_verified=False)
    assert await _is_invitee(db, world.site_id, unverified) is False
