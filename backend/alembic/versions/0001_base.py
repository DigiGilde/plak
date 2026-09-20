"""0001_base: initial schema (spec §12, §3).

Revision ID: 0001_base
Revises:
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_base"
down_revision = None
branch_labels = None
depends_on = None

# Values hardcoded on purpose (not imported from plak.constants): a migration
# is a frozen snapshot. test_migrations.py guards that these lists and the
# application constants AccessBase and Role do not drift apart, order
# included: in SQL that order is the sort order of the type.
_ACCESS_BASE_VALUES = ("public", "sso", "site_team", "nobody")
_ROLE_VALUES = ("reader", "editor", "admin")
_RESERVED_SLUGS_SQL = "'.well-known', 'cli-koppelen', 'favicon.ico', 'robots.txt'"
_ACTOR_KIND_VALUES = ("member", "ci", "system", "anonymous")
_CI_PROVIDER_VALUES = ("github", "forgejo")
_CLI_DEVICE_STATUS_VALUES = ("pending", "approved", "denied")
_MEMBER_LANGUAGE_VALUES = ("nl", "en")

# What makes a group unmanageable is losing its last admin, and that can happen
# without a DELETE too, namely by demoting that last admin. Hence AFTER DELETE
# OR UPDATE OF role.
#
# The existence check on the group is indispensable: when someone deletes the
# group itself, the cascade clears the memberships first, and a check on "does
# the group still keep an admin" would block that deletion.
_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION guard_last_group_admin() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM groups WHERE id = OLD.group_id) THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM group_members WHERE group_id = OLD.group_id AND role = 'admin'
    ) THEN
        RAISE EXCEPTION 'groep % zou zonder beheerder achterblijven', OLD.group_id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

_GUARD_TRIGGER = """
CREATE CONSTRAINT TRIGGER ck_groups_keep_one_admin
    AFTER DELETE OR UPDATE OF role ON group_members
    DEFERRABLE INITIALLY IMMEDIATE
    FOR EACH ROW EXECUTE FUNCTION guard_last_group_admin();
"""


def _enum(*values: str, name: str, create_type: bool) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=create_type)


def upgrade() -> None:
    bind = op.get_bind()

    _enum(*_ACCESS_BASE_VALUES, name="access_base", create_type=False).create(bind, checkfirst=True)
    _enum(*_ROLE_VALUES, name="role", create_type=False).create(bind, checkfirst=True)
    _enum("admin", "member", name="platform_role", create_type=False).create(bind, checkfirst=True)
    _enum("active", "deactivated", name="member_status", create_type=False).create(bind, checkfirst=True)
    _enum(*_MEMBER_LANGUAGE_VALUES, name="member_language", create_type=False).create(bind, checkfirst=True)
    _enum("live", "preview", name="version_target", create_type=False).create(bind, checkfirst=True)
    _enum("active", "revoked", name="key_status", create_type=False).create(bind, checkfirst=True)
    _enum(*_CI_PROVIDER_VALUES, name="ci_provider", create_type=False).create(bind, checkfirst=True)
    _enum(*_CLI_DEVICE_STATUS_VALUES, name="cli_device_status", create_type=False).create(bind, checkfirst=True)
    _enum(*_ACTOR_KIND_VALUES, name="actor_kind", create_type=False).create(bind, checkfirst=True)

    op.create_table(
        "members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sso_subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column(
            "platform_role",
            _enum("admin", "member", name="platform_role", create_type=False),
            nullable=False,
            server_default="member",
        ),
        sa.Column(
            "status",
            _enum("active", "deactivated", name="member_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        # Nullable: NULL is "follow my browser", the state every member starts
        # in. A third enum value would be a language that is not a language.
        sa.Column(
            "language",
            _enum(*_MEMBER_LANGUAGE_VALUES, name="member_language", create_type=False),
            nullable=True,
        ),
        sa.UniqueConstraint("sso_subject", name="uq_members_sso_subject"),
        sa.CheckConstraint("email = lower(email)", name="ck_members_email_lowercase"),
    )

    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "default_access_base",
            _enum(*_ACCESS_BASE_VALUES, name="access_base", create_type=False),
            nullable=False,
        ),
        sa.Column("default_access_keys", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_access_invitees", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("slug", name="uq_groups_slug"),
        sa.CheckConstraint(f"slug NOT IN ({_RESERVED_SLUGS_SQL})", name="ck_groups_slug_reserved"),
    )

    op.create_table(
        "group_members",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Deliberately no default: whoever adds someone to a group picks the
        # role, instead of silently getting the strongest one thrown in.
        sa.Column("role", _enum(*_ROLE_VALUES, name="role", create_type=False), nullable=False),
    )

    op.create_table(
        "sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column(
            "access_base",
            _enum(*_ACCESS_BASE_VALUES, name="access_base", create_type=False),
            nullable=False,
        ),
        sa.Column("access_keys", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("access_invitees", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("external_sources", sa.Boolean(), nullable=False, server_default=sa.true()),
        # The FK to versions.id follows below, after creating the versions table
        # (circular dependency sites <-> versions).
        sa.Column("live_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("group_id", "slug", name="uq_sites_group_slug"),
    )

    op.create_table(
        "site_members",
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", _enum(*_ROLE_VALUES, name="role", create_type=False), nullable=False),
        sa.Column(
            "added_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "site_repositories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", _enum(*_CI_PROVIDER_VALUES, name="ci_provider", create_type=False), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("live_branch", sa.String(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("site_id", name="uq_site_repositories_site"),
    )

    op.create_table(
        "cli_device_authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_selector", sa.String(), nullable=False),
        sa.Column("device_hash", sa.String(), nullable=False),
        sa.Column("user_code_hash", sa.String(), nullable=False),
        sa.Column("client_name", sa.String(), nullable=True),
        sa.Column("ip_truncated", sa.String(), nullable=True),
        sa.Column(
            "status",
            _enum(*_CLI_DEVICE_STATUS_VALUES, name="cli_device_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("device_selector", name="uq_cli_device_authorizations_selector"),
        sa.UniqueConstraint("user_code_hash", name="uq_cli_device_authorizations_user_code"),
        sa.CheckConstraint(
            "status <> 'approved' OR member_id IS NOT NULL", name="ck_cli_device_authorizations_member"
        ),
    )

    op.create_table(
        "cli_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("access_selector", sa.String(), nullable=False),
        sa.Column("access_hash", sa.String(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("access_selector", name="uq_cli_sessions_access_selector"),
    )

    op.create_table(
        "cli_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cli_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selector", sa.String(), nullable=False),
        sa.Column("verifier_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("selector", name="uq_cli_refresh_tokens_selector"),
    )

    op.create_table(
        "versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target",
            _enum("live", "preview", name="version_target", create_type=False),
            nullable=False,
        ),
        sa.Column("storage_ref", sa.String(), nullable=False),
        sa.Column(
            "member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("members.id", ondelete="SET NULL"), nullable=True
        ),
        # A CI deploy records the repository it came from as text
        # ("github.com/owner/repo"), not as a reference: unlinking the
        # repository must not rewrite or orphan the deploy history.
        sa.Column("ci_repository", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(member_id IS NOT NULL AND ci_repository IS NULL) OR "
            "(member_id IS NULL AND ci_repository IS NOT NULL)",
            name="ck_versions_origin",
        ),
    )

    op.create_foreign_key(
        "fk_sites_live_version_id",
        "sites",
        "versions",
        ["live_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "previews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "access_base_override",
            _enum(*_ACCESS_BASE_VALUES, name="access_base", create_type=False),
            nullable=True,
        ),
        sa.Column("access_keys_override", sa.Boolean(), nullable=True),
        sa.Column("access_invitees_override", sa.Boolean(), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("site_id", "ref", name="uq_previews_site_ref"),
        # An override is one whole policy: all three together or none of them.
        sa.CheckConstraint(
            "(access_base_override IS NULL AND access_keys_override IS NULL "
            "AND access_invitees_override IS NULL) OR "
            "(access_base_override IS NOT NULL AND access_keys_override IS NOT NULL "
            "AND access_invitees_override IS NOT NULL)",
            name="ck_previews_access_override",
        ),
    )

    op.create_table(
        "invitees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identifier", sa.String(), nullable=False),
        sa.Column(
            "added_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("site_id", "identifier", name="uq_invitees_site_identifier"),
        sa.CheckConstraint("identifier = lower(identifier)", name="ck_invitees_identifier_lowercase"),
    )

    op.create_table(
        "access_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("selector", sa.String(), nullable=False),
        sa.Column("verifier_hash", sa.String(), nullable=False),
        sa.Column(
            "status",
            _enum("active", "revoked", name="key_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("selector", name="uq_access_keys_selector"),
    )

    op.create_table(
        "audit_log_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_kind",
            _enum(*_ACTOR_KIND_VALUES, name="actor_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("actor_pseudonym", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=True),
        sa.Column("refs", postgresql.JSONB(), nullable=True),
        sa.Column("ip_truncated", sa.String(), nullable=True),
        sa.Column("ip_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "content_viewers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sso_subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sso_subject", name="uq_content_viewers_sso_subject"),
    )

    # Both membership tables are indexed only on their composite PK, which
    # starts with group_id and site_id respectively. The overview query looks
    # up on member_id ("which groups and sites am I in"), and there are two
    # of those.
    op.create_index("ix_group_members_member_id", "group_members", ["member_id"])
    op.create_index("ix_site_members_member_id", "site_members", ["member_id"])

    # Keyset pagination for the audit read path, and the retention scan.
    op.create_index(
        "ix_audit_log_entries_occurred_at_id",
        "audit_log_entries",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
    )
    # The content_viewers retention scan (audit/retention.py) filters on this.
    op.create_index("ix_content_viewers_last_seen_at", "content_viewers", ["last_seen_at"])
    # The expiry sweeps (previews/cleanup_job.py) and the CLI session list per member.
    op.create_index("ix_cli_device_authorizations_expires_at", "cli_device_authorizations", ["expires_at"])
    op.create_index("ix_cli_sessions_member_id", "cli_sessions", ["member_id"])
    op.create_index("ix_cli_refresh_tokens_session_id", "cli_refresh_tokens", ["session_id"])

    # Append-only: triggers refuse UPDATE always, and DELETE until a row has
    # outlived its retention. This is the second layer next to account
    # separation (the runtime account has INSERT/SELECT only; the cleanup
    # account may DELETE, but only what this trigger lets through).
    op.execute(
        """
        CREATE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'auditlog is append-only: % niet toegestaan', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_update
        BEFORE UPDATE ON audit_log_entries
        FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()
        """
    )
    # The retention lives here and nowhere else the cleanup could override:
    # the job asks the database what has expired. Shortening a term is a
    # schema change, which is what a retention decision should cost.
    # docs/audit-log.md; audit/vocabulary.py mirrors the tiers for reporting.
    op.execute(
        """
        CREATE FUNCTION audit_log_retention(action text, result text) RETURNS interval AS $$
            SELECT CASE
                WHEN (action, result) IN (
                    ('content_access', 'allowed'),
                    ('login', 'allowed'),
                    ('logout', 'allowed'),
                    ('cli_logout', 'allowed')
                ) THEN interval '90 days'
                ELSE interval '3 years'
            END;
        $$ LANGUAGE sql IMMUTABLE
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_log_delete_after_retention() RETURNS trigger AS $$
        BEGIN
            IF OLD.occurred_at > now() - audit_log_retention(OLD.action, OLD.result) THEN
                RAISE EXCEPTION 'auditlog is append-only tot de bewaartermijn om is: % mag nog niet weg', OLD.id;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_delete_after_retention
        BEFORE DELETE ON audit_log_entries
        FOR EACH ROW EXECUTE FUNCTION audit_log_delete_after_retention()
        """
    )

    # Same shape as audit_log's own delete guard: a fixed 90-day term (no
    # per-row function needed, content_viewers has only the one term), so the
    # cleanup account's DELETE is bounded by the trigger too, not only by the
    # WHERE clause in audit/retention.py.
    op.execute(
        """
        CREATE FUNCTION content_viewer_delete_after_retention() RETURNS trigger AS $$
        BEGIN
            IF OLD.last_seen_at > now() - interval '90 days' THEN
                RAISE EXCEPTION 'content_viewers is pas te verwijderen na de bewaartermijn: % mag nog niet weg', OLD.id;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER content_viewer_delete_after_retention
        BEFORE DELETE ON content_viewers
        FOR EACH ROW EXECUTE FUNCTION content_viewer_delete_after_retention()
        """
    )

    op.execute(_GUARD_FUNCTION)
    op.execute(_GUARD_TRIGGER)


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("DROP TRIGGER IF EXISTS ck_groups_keep_one_admin ON group_members")
    op.execute("DROP FUNCTION IF EXISTS guard_last_group_admin()")

    op.execute("DROP TRIGGER IF EXISTS content_viewer_delete_after_retention ON content_viewers")
    op.execute("DROP FUNCTION IF EXISTS content_viewer_delete_after_retention()")
    op.execute("DROP TRIGGER IF EXISTS audit_log_delete_after_retention ON audit_log_entries")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log_entries")
    op.execute("DROP FUNCTION IF EXISTS audit_log_delete_after_retention()")
    op.execute("DROP FUNCTION IF EXISTS audit_log_retention(text, text)")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    op.drop_index("ix_audit_log_entries_occurred_at_id", table_name="audit_log_entries")
    op.drop_index("ix_content_viewers_last_seen_at", table_name="content_viewers")
    op.drop_index("ix_cli_refresh_tokens_session_id", table_name="cli_refresh_tokens")
    op.drop_index("ix_cli_sessions_member_id", table_name="cli_sessions")
    op.drop_index("ix_cli_device_authorizations_expires_at", table_name="cli_device_authorizations")

    op.drop_index("ix_site_members_member_id", table_name="site_members")
    op.drop_index("ix_group_members_member_id", table_name="group_members")

    op.drop_table("content_viewers")
    op.drop_table("audit_log_entries")
    op.drop_table("access_keys")
    op.drop_table("invitees")
    op.drop_table("previews")
    op.drop_constraint("fk_sites_live_version_id", "sites", type_="foreignkey")
    op.drop_table("versions")
    op.drop_table("cli_refresh_tokens")
    op.drop_table("cli_sessions")
    op.drop_table("cli_device_authorizations")
    op.drop_table("site_repositories")
    op.drop_table("site_members")
    op.drop_table("sites")
    op.drop_table("group_members")
    op.drop_table("groups")
    op.drop_table("members")

    for name in (
        "actor_kind",
        "cli_device_status",
        "ci_provider",
        "key_status",
        "version_target",
        "member_language",
        "member_status",
        "platform_role",
        "role",
        "access_base",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
