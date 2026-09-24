"""Tests for the migrations (spec §12, §3): schema, enum
types, CHECKs, UNIQUE, the append-only audit log triggers and the guard that a
group never ends up without an admin."""

from __future__ import annotations

import pathlib
import uuid
from datetime import timedelta

import asyncpg
import pytest
from helpers_audit import insert_aged_audit_row

from plak import i18n
from plak.audit import vocabulary
from plak.constants import RESERVED_SLUGS, ROLE_RANK, AccessBase, Role
from plak.models.identity import MemberLanguage

pytestmark = pytest.mark.asyncio


async def _make_member(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO members (id, sso_subject, email, platform_role, status)
        VALUES ($1, $2, $3, 'member', 'active')
        RETURNING id
        """,
        uuid.uuid4(),
        f"sub-{uuid.uuid4().hex}",
        f"lid-{uuid.uuid4().hex}@example.org",
    )


async def _make_group(conn: asyncpg.Connection, base: str = "public") -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO groups (id, slug, name, default_access_base)
        VALUES ($1, $2, 'Testgroep', $3)
        RETURNING id
        """,
        uuid.uuid4(),
        f"groep-{uuid.uuid4().hex[:12]}",
        base,
    )


async def _make_site(conn: asyncpg.Connection, group_id: uuid.UUID, base: str = "public") -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO sites (id, group_id, slug, title, access_base)
        VALUES ($1, $2, $3, 'Testsite', $4)
        RETURNING id
        """,
        uuid.uuid4(),
        group_id,
        f"site-{uuid.uuid4().hex[:12]}",
        base,
    )


async def _make_version(conn: asyncpg.Connection, site_id: uuid.UUID, member_id: uuid.UUID) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO versions (id, site_id, target, storage_ref, member_id)
        VALUES ($1, $2, 'preview', 'opslag/1', $3)
        RETURNING id
        """,
        uuid.uuid4(),
        site_id,
        member_id,
    )


async def _make_group_member(
    conn: asyncpg.Connection, group_id: uuid.UUID, member_id: uuid.UUID, role: str = "beheerder"
) -> None:
    await conn.execute(
        "INSERT INTO group_members (group_id, member_id, role) VALUES ($1, $2, $3)", group_id, member_id, role
    )


def _latest_revision() -> str:
    """The head revision according to the migration files themselves (not a
    fixed value, which would topple over on every new migration without
    guarding anything)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = pathlib.Path(__file__).resolve().parents[1]
    return ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_current_head()


async def test_migration_runs_on_empty_database(db_connection: asyncpg.Connection) -> None:
    """The database is at the latest revision. The expectation comes from the
    migration files themselves, so a new migration does not break this test but
    a half-run migration does."""
    version = await db_connection.fetchval("SELECT version_num FROM alembic_version")
    assert version == _latest_revision()


async def test_all_core_tables_exist(db_connection: asyncpg.Connection) -> None:
    expected_tables = {
        "members",
        "groups",
        "group_members",
        "site_members",
        "sites",
        "versions",
        "previews",
        "invitees",
        "access_keys",
        "site_repositories",
        "cli_device_authorizations",
        "cli_sessions",
        "cli_refresh_tokens",
        "audit_log_entries",
    }
    rows = await db_connection.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    present = {row["table_name"] for row in rows}
    assert expected_tables <= present


async def test_audit_log_update_refused_by_trigger(db_connection: asyncpg.Connection) -> None:
    audit_id = uuid.uuid4()
    await db_connection.execute(
        """
        INSERT INTO audit_log_entries (id, actor_kind, action, result)
        VALUES ($1, 'system', 'test_actie', 'allowed')
        """,
        audit_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute("UPDATE audit_log_entries SET action = 'gewijzigd' WHERE id = $1", audit_id)


async def test_audit_log_delete_refused_by_trigger(db_connection: asyncpg.Connection) -> None:
    audit_id = uuid.uuid4()
    await db_connection.execute(
        """
        INSERT INTO audit_log_entries (id, actor_kind, action, result)
        VALUES ($1, 'system', 'test_actie', 'allowed')
        """,
        audit_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute("DELETE FROM audit_log_entries WHERE id = $1", audit_id)


async def _audit_row(conn: asyncpg.Connection, action: str, result: str, age: timedelta) -> uuid.UUID:
    return await insert_aged_audit_row(conn, action, result, age)


async def _deletable(conn: asyncpg.Connection, audit_id: uuid.UUID) -> bool:
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM audit_log_entries WHERE id = $1", audit_id)
    except asyncpg.PostgresError:
        return False
    return True


@pytest.mark.parametrize(("action", "result"), sorted(vocabulary.SHORT_RETENTION))
async def test_looking_goes_after_ninety_days(
    db_connection: asyncpg.Connection, action: str, result: str
) -> None:
    fresh = await _audit_row(db_connection, action, result, timedelta(days=89))
    expired = await _audit_row(db_connection, action, result, timedelta(days=91))

    assert not await _deletable(db_connection, fresh)
    assert await _deletable(db_connection, expired)


@pytest.mark.parametrize(
    ("action", "result"),
    [
        ("content_access", "refused"),
        ("content_access", "login_redirect"),
        ("login", "refused"),
        ("admin_access", "refused"),
        ("member_platform_role", "allowed"),
        ("deploy", "allowed"),
        ("audit_read", "allowed"),
        # Approving a CLI login hands out a credential that lives up to 90
        # days; the record of who granted it must outlive it.
        ("cli_login", "allowed"),
        ("cli_token_issued", "allowed"),
        ("cli_session_revoke", "allowed"),
        ("cli_refresh_reuse", "refused"),
        ("cli_logout", "refused"),
    ],
)
async def test_everything_else_stays_three_years(
    db_connection: asyncpg.Connection, action: str, result: str
) -> None:
    """Every refusal and every management act can become part of an incident,
    and BIO2 5.28.01 keeps those for three years. A refusal of the same age as
    an expired viewing row must still be refused."""
    a_year = await _audit_row(db_connection, action, result, timedelta(days=400))
    # Three calendar years plus the leap days they can hold.
    three_years = await _audit_row(db_connection, action, result, timedelta(days=3 * 366 + 1))

    assert not await _deletable(db_connection, a_year)
    assert await _deletable(db_connection, three_years)


async def test_the_database_and_the_code_agree_on_the_terms(db_connection: asyncpg.Connection) -> None:
    for action, result in vocabulary.SHORT_RETENTION:
        days = await db_connection.fetchval(
            "SELECT extract(day FROM audit_log_retention($1, $2))::int", action, result
        )
        assert days == vocabulary.SHORT_RETENTION_DAYS
    long_term = await db_connection.fetchval(
        "SELECT audit_log_retention('admin_access', 'refused') = interval '3 years'"
    )
    assert long_term


async def test_content_viewers_deletable_only_after_ninety_days(db_connection: asyncpg.Connection) -> None:
    """The BEFORE DELETE trigger on content_viewers bounds the retention job,
    not the WHERE clause in audit/retention.py: a row that is still within its
    term stays, whoever issues the DELETE."""
    fresh = await db_connection.fetchval(
        "INSERT INTO content_viewers (id, sso_subject, email) VALUES ($1, $2, $3) RETURNING id",
        uuid.uuid4(),
        f"sub-{uuid.uuid4().hex}",
        "viewer@example.nl",
    )
    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute("DELETE FROM content_viewers WHERE id = $1", fresh)

    expired = await db_connection.fetchval(
        """
        INSERT INTO content_viewers (id, sso_subject, email, last_seen_at)
        VALUES ($1, $2, $3, now() - interval '91 days')
        RETURNING id
        """,
        uuid.uuid4(),
        f"sub-{uuid.uuid4().hex}",
        "viewer@example.nl",
    )
    await db_connection.execute("DELETE FROM content_viewers WHERE id = $1", expired)
    assert await db_connection.fetchval("SELECT count(*) FROM content_viewers WHERE id = $1", expired) == 0


async def _enum_labels(conn: asyncpg.Connection, type_name: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT e.enumlabel
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = $1
        ORDER BY e.enumsortorder
        """,
        type_name,
    )
    return [row["enumlabel"] for row in rows]


@pytest.mark.parametrize(
    ("type_name", "enum_class", "count"),
    [("access_base", AccessBase, 4), ("role", Role, 3), ("member_language", MemberLanguage, 2)],
)
async def test_shared_enum_type_used_exactly_the_application_values(
    db_connection: asyncpg.Connection, type_name: str, enum_class: type, count: int
) -> None:
    """The migrations write their labels hard-coded; here they are held against
    the application constants, so the two cannot drift apart unnoticed."""
    db_values_set = set(await _enum_labels(db_connection, type_name))
    application_values = {member.value for member in enum_class}

    assert db_values_set == application_values
    assert len(application_values) == count


async def test_member_language_matches_the_languages_the_app_speaks() -> None:
    """The column can only ever hold a language the interface has a catalogue
    for; a third value would be storable and unreadable."""
    assert {language.value for language in MemberLanguage} == set(i18n.SUPPORTED)


async def test_member_language_is_nullable(db_connection: asyncpg.Connection) -> None:
    """NULL is "follow my browser", the state every member starts in."""
    member_id = await _make_member(db_connection)
    assert await db_connection.fetchval("SELECT language FROM members WHERE id = $1", member_id) is None


async def test_role_enum_sorts_of_narrow_to_wide(db_connection: asyncpg.Connection) -> None:
    """The declaration order in the migration is load-bearing as soon as roles
    are compared in SQL, so it has to give the same ordering as ROLE_RANK."""
    labels = await _enum_labels(db_connection, "role")
    assert labels == [role.value for role in sorted(ROLE_RANK, key=lambda role: ROLE_RANK[role])]
    assert labels == ["reader", "editor", "admin"]


@pytest.mark.parametrize("value", [member.value for member in AccessBase])
async def test_group_default_access_base_accepts_every_valid_value(
    db_connection: asyncpg.Connection, value: str
) -> None:
    group_id = await _make_group(db_connection, base=value)
    assert group_id is not None


@pytest.mark.parametrize("value", [member.value for member in AccessBase])
async def test_site_access_base_accepts_every_valid_value(
    db_connection: asyncpg.Connection, value: str
) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id, base=value)
    assert site_id is not None


@pytest.mark.parametrize("value", [member.value for member in AccessBase])
async def test_preview_access_override_accepts_every_valid_value(
    db_connection: asyncpg.Connection, value: str
) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    version_id = await _make_version(db_connection, site_id, member_id)

    preview_id = await db_connection.fetchval(
        """
        INSERT INTO previews (
            id, site_id, ref, version_id,
            access_base_override, access_keys_override, access_invitees_override
        )
        VALUES ($1, $2, 'pr-1', $3, $4, false, false)
        RETURNING id
        """,
        uuid.uuid4(),
        site_id,
        version_id,
        value,
    )
    assert preview_id is not None


@pytest.mark.parametrize(
    "columns",
    [
        "access_base_override",
        "access_keys_override",
        "access_base_override, access_invitees_override",
    ],
)
async def test_ck_previews_access_override_refuses_half_an_override(
    db_connection: asyncpg.Connection, columns: str
) -> None:
    """An override is one whole policy. Half of one would mean a base from the
    preview with extras silently borrowed from the site, which is exactly the
    kind of drift the single enum type is there to prevent."""
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    version_id = await _make_version(db_connection, site_id, member_id)
    values = ", ".join(
        "'sso'" if name.strip() == "access_base_override" else "true" for name in columns.split(",")
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            # Both halves of the interpolation come from the parametrize list.
            statement = (
                f"INSERT INTO previews (id, site_id, ref, version_id, {columns}) "  # noqa: S608
                f"VALUES ($1, $2, 'pr-half', $3, {values})"
            )
            await db_connection.execute(
                statement,
                uuid.uuid4(),
                site_id,
                version_id,
            )


async def test_external_sources_defaults_to_on(db_connection: asyncpg.Connection) -> None:
    """Aan, tenzij je het uitzet: a row that never names the column gets the
    permissive value, and the SPA switch is the restriction."""
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    assert await db_connection.fetchval(
        "SELECT external_sources FROM sites WHERE id = $1", site_id
    ) is True


async def test_access_base_enum_refuses_unknown_value(db_connection: asyncpg.Connection) -> None:
    group_id = uuid.uuid4()
    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO groups (id, slug, name, default_access_base)
                VALUES ($1, 'onbekend-niveau', 'Test', 'onbekend')
                """,
                group_id,
            )


async def test_ck_versions_origin_refuses_neither(db_connection: asyncpg.Connection) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO versions (id, site_id, target, storage_ref)
                VALUES ($1, $2, 'preview', 'opslag/1')
                """,
                uuid.uuid4(),
                site_id,
            )


async def test_ck_versions_origin_refuses_both(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO versions (id, site_id, target, storage_ref, member_id, ci_repository)
                VALUES ($1, $2, 'preview', 'opslag/1', $3, 'github.com/minbzk/website')
                """,
                uuid.uuid4(),
                site_id,
                member_id,
            )


async def test_ck_versions_origin_allows_exactly_one(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    version_id = await _make_version(db_connection, site_id, member_id)
    assert version_id is not None
    ci_version_id = await db_connection.fetchval(
        """
        INSERT INTO versions (id, site_id, target, storage_ref, ci_repository)
        VALUES ($1, $2, 'live', 'opslag/ci', 'github.com/minbzk/website')
        RETURNING id
        """,
        uuid.uuid4(),
        site_id,
    )
    assert ci_version_id is not None


async def test_unique_site_id_ref_enforced_on_preview(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    version_1 = await _make_version(db_connection, site_id, member_id)
    version_2 = await _make_version(db_connection, site_id, member_id)

    await db_connection.execute(
        """
        INSERT INTO previews (id, site_id, ref, version_id)
        VALUES ($1, $2, 'pr-42', $3)
        """,
        uuid.uuid4(),
        site_id,
        version_1,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO previews (id, site_id, ref, version_id)
                VALUES ($1, $2, 'pr-42', $3)
                """,
                uuid.uuid4(),
                site_id,
                version_2,
            )


async def test_a_site_has_at_most_one_repository_and_loses_it_with_the_site(
    db_connection: asyncpg.Connection,
) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)
    insert = """
        INSERT INTO site_repositories (id, site_id, provider, host, owner, repo, repository_id, owner_id)
        VALUES ($1, $2, 'github', 'https://github.com', 'minbzk', 'website', 1, 2)
    """
    await db_connection.execute(insert, uuid.uuid4(), site_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db_connection.transaction():
            await db_connection.execute(insert, uuid.uuid4(), site_id)

    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        async with db_connection.transaction():
            await db_connection.execute(
                insert.replace("'github'", "'gitlab'"), uuid.uuid4(), await _make_site(db_connection, group_id)
            )

    await db_connection.execute("DELETE FROM sites WHERE id = $1", site_id)
    assert await db_connection.fetchval("SELECT count(*) FROM site_repositories") == 0


async def test_an_approved_device_authorization_names_its_member(db_connection: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO cli_device_authorizations
                    (id, device_selector, device_hash, user_code_hash, status, expires_at)
                VALUES ($1, 'sel', 'hash', 'code', 'approved', now() + interval '10 minutes')
                """,
                uuid.uuid4(),
            )


async def test_cli_sessions_and_refresh_tokens_go_with_their_member(db_connection: asyncpg.Connection) -> None:
    member_id = await _make_member(db_connection)
    session_id = await db_connection.fetchval(
        """
        INSERT INTO cli_sessions
            (id, member_id, expires_at, max_expires_at, access_selector, access_hash, access_expires_at)
        VALUES ($1, $2, now() + interval '30 days', now() + interval '90 days', 'sel', 'hash', now())
        RETURNING id
        """,
        uuid.uuid4(),
        member_id,
    )
    await db_connection.execute(
        "INSERT INTO cli_refresh_tokens (id, session_id, selector, verifier_hash) VALUES ($1, $2, 'r', 'h')",
        uuid.uuid4(),
        session_id,
    )
    await db_connection.execute("DELETE FROM members WHERE id = $1", member_id)
    assert await db_connection.fetchval("SELECT count(*) FROM cli_sessions") == 0
    assert await db_connection.fetchval("SELECT count(*) FROM cli_refresh_tokens") == 0


async def test_expiry_is_required_on_keys(db_connection: asyncpg.Connection) -> None:
    group_id = await _make_group(db_connection)
    site_id = await _make_site(db_connection, group_id)

    with pytest.raises(asyncpg.exceptions.NotNullViolationError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO access_keys (id, site_id, label, selector, verifier_hash)
                VALUES ($1, $2, 'test', $3, 'hash')
                """,
                uuid.uuid4(),
                site_id,
                f"selector-{uuid.uuid4().hex[:8]}",
            )


@pytest.mark.parametrize("reserved", sorted(RESERVED_SLUGS))
async def test_group_slug_reserved_refused(
    db_connection: asyncpg.Connection, reserved: str
) -> None:
    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                """
                INSERT INTO groups (id, slug, name, default_access_base)
                VALUES ($1, $2, 'Test', 'public')
                """,
                uuid.uuid4(),
                reserved,
            )


async def test_ck_group_slug_covers_exactly_the_constant(
    db_connection: asyncpg.Connection,
) -> None:
    definition = await db_connection.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'ck_groups_slug_reserved'"
    )
    assert definition is not None
    for slug in RESERVED_SLUGS:
        assert slug in definition


async def test_last_group_admin_refused_by_trigger(db_connection: asyncpg.Connection) -> None:
    """The application already refuses this with a 409; the trigger is the
    layer underneath, so a groups cannot run empty through a direct DELETE
    either."""
    group_id, member_id = uuid.uuid4(), uuid.uuid4()
    await db_connection.execute(
        "INSERT INTO groups (id, slug, name, default_access_base) VALUES ($1, $2, $3, 'site_team')",
        group_id,
        f"trigger-{group_id.hex[:8]}",
        "Triggerproef",
    )
    await db_connection.execute(
        "INSERT INTO members (id, sso_subject, email, platform_role, status) VALUES ($1, $2, $3, 'member', 'active')",
        member_id,
        f"sub-{member_id.hex[:8]}",
        f"{member_id.hex[:8]}@example.nl",
    )
    await db_connection.execute(
        "INSERT INTO group_members (group_id, member_id, role) VALUES ($1, $2, 'admin')",
        group_id,
        member_id,
    )

    with pytest.raises(asyncpg.PostgresError):
        async with db_connection.transaction():
            await db_connection.execute(
                "DELETE FROM group_members WHERE group_id = $1 AND member_id = $2", group_id, member_id
            )

    remaining = await db_connection.fetchval(
        "SELECT count(*) FROM group_members WHERE group_id = $1", group_id
    )
    assert remaining == 1


async def test_deleting_a_group_does_clean_up_the_last_admin_role(
    db_connection: asyncpg.Connection,
) -> None:
    """The trigger must not block the cascade: when the groep itself is deleted
    the groep no longer exists, and an empty membership is then exactly right."""
    group_id, member_id = uuid.uuid4(), uuid.uuid4()
    await db_connection.execute(
        "INSERT INTO groups (id, slug, name, default_access_base) VALUES ($1, $2, $3, 'site_team')",
        group_id,
        f"cascade-{group_id.hex[:8]}",
        "Cascadeproef",
    )
    await db_connection.execute(
        "INSERT INTO members (id, sso_subject, email, platform_role, status) VALUES ($1, $2, $3, 'member', 'active')",
        member_id,
        f"sub-{member_id.hex[:8]}",
        f"{member_id.hex[:8]}@example.nl",
    )
    await db_connection.execute(
        "INSERT INTO group_members (group_id, member_id, role) VALUES ($1, $2, 'admin')",
        group_id,
        member_id,
    )

    await db_connection.execute("DELETE FROM groups WHERE id = $1", group_id)

    assert await db_connection.fetchval("SELECT count(*) FROM group_members WHERE group_id = $1", group_id) == 0
