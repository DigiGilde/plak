"""0005_slug_namespace: the current and the retired slugs of groups and sites.

`group_slugs` holds every slug a group has or recently had, `site_slugs` the
same for the sites within one group; `retired_at` is NULL on the current one.
Their primary keys keep a slug unique across current and retired slugs alike,
so a slug a group or site gave up stays reserved for it while its old address
still redirects, and nobody else can claim it meanwhile. `groups.slug` and
`sites.slug` stay the current slug every lookup reads.

The triggers below are what fills these tables: whatever changes a slug, the
API or a statement by hand, keeps the namespace in step. The app reads them,
and deletes a retired row once its redirect has ended (the nightly cleanup);
it never writes a current row. A rename also makes the repository link of the
renamed site, or of every site of a renamed group, require the site id (0004):
a link that still takes a token without one would otherwise follow its site
onto an address that used to be another site's, and receive what the
workflows still naming that address publish.

Never `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE` in these triggers: when
its WHERE does not hold, PostgreSQL skips the row without an error, and a slug
reserved for another group or site would be taken over in silence.

The downgrade drops both tables, and with them every reservation and every
redirect at once: each retired slug is free again the moment it runs.

Revision ID: 0005_slug_namespace
Revises: 0004_site_id_required
Create Date: 2026-10-08
"""

from __future__ import annotations

from alembic import op

revision = "0005_slug_namespace"
down_revision = "0004_site_id_required"
branch_labels = None
depends_on = None

# One statement each: the asyncpg driver prepares what it executes, and a
# prepared statement holds a single command.
_TABLES = (
    """
    CREATE TABLE group_slugs (
        slug        text NOT NULL,
        group_id    uuid NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
        retired_at  timestamptz,
        CONSTRAINT pk_group_slugs PRIMARY KEY (slug)
    )
    """,
    "CREATE UNIQUE INDEX uq_group_slugs_current ON group_slugs (group_id) WHERE retired_at IS NULL",
    "CREATE INDEX ix_group_slugs_group_id ON group_slugs (group_id)",
    """
    CREATE TABLE site_slugs (
        group_id    uuid NOT NULL REFERENCES groups (id) ON DELETE CASCADE,
        slug        text NOT NULL,
        site_id     uuid NOT NULL REFERENCES sites (id) ON DELETE CASCADE,
        retired_at  timestamptz,
        CONSTRAINT pk_site_slugs PRIMARY KEY (group_id, slug)
    )
    """,
    "CREATE UNIQUE INDEX uq_site_slugs_current ON site_slugs (site_id) WHERE retired_at IS NULL",
    "CREATE INDEX ix_site_slugs_site_id ON site_slugs (site_id)",
    # A foreign key locks the table it references in SHARE ROW EXCLUSIVE mode
    # until the commit, so from here on no group or site is created or renamed
    # (an instance still running mid deploy) between this copy and the
    # triggers below.
    "INSERT INTO group_slugs (slug, group_id) SELECT slug, id FROM groups",
    "INSERT INTO site_slugs (group_id, slug, site_id) SELECT group_id, slug, id FROM sites",
)

# Retire the old slug before the new one becomes current: the other order
# would hold two current rows for a moment, which uq_*_slugs_current refuses.
# A slug another group or site still holds makes the INSERT fail on the
# primary key; one it holds as its current slug fails earlier, on
# uq_groups_slug or uq_sites_group_slug.
_FUNCTIONS = (
    """
    CREATE FUNCTION group_slugs_on_insert() RETURNS trigger AS $$
    BEGIN
        INSERT INTO group_slugs (slug, group_id) VALUES (NEW.slug, NEW.id);
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE FUNCTION group_slugs_on_rename() RETURNS trigger AS $$
    BEGIN
        UPDATE group_slugs SET retired_at = now() WHERE slug = OLD.slug AND group_id = NEW.id;
        UPDATE group_slugs SET retired_at = NULL WHERE slug = NEW.slug AND group_id = NEW.id;
        IF NOT FOUND THEN
            INSERT INTO group_slugs (slug, group_id) VALUES (NEW.slug, NEW.id);
        END IF;
        UPDATE site_repositories SET site_id_required = true
            WHERE site_id IN (SELECT id FROM sites WHERE group_id = NEW.id);
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE FUNCTION site_slugs_on_insert() RETURNS trigger AS $$
    BEGIN
        INSERT INTO site_slugs (group_id, slug, site_id) VALUES (NEW.group_id, NEW.slug, NEW.id);
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE FUNCTION site_slugs_on_rename() RETURNS trigger AS $$
    BEGIN
        UPDATE site_slugs SET retired_at = now()
            WHERE group_id = NEW.group_id AND slug = OLD.slug AND site_id = NEW.id;
        UPDATE site_slugs SET retired_at = NULL
            WHERE group_id = NEW.group_id AND slug = NEW.slug AND site_id = NEW.id;
        IF NOT FOUND THEN
            INSERT INTO site_slugs (group_id, slug, site_id) VALUES (NEW.group_id, NEW.slug, NEW.id);
        END IF;
        UPDATE site_repositories SET site_id_required = true WHERE site_id = NEW.id;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
)

_TRIGGERS = (
    """
    CREATE TRIGGER group_slugs_insert AFTER INSERT ON groups
        FOR EACH ROW EXECUTE FUNCTION group_slugs_on_insert()
    """,
    """
    CREATE TRIGGER group_slugs_rename AFTER UPDATE OF slug ON groups
        FOR EACH ROW WHEN (OLD.slug IS DISTINCT FROM NEW.slug) EXECUTE FUNCTION group_slugs_on_rename()
    """,
    """
    CREATE TRIGGER site_slugs_insert AFTER INSERT ON sites
        FOR EACH ROW EXECUTE FUNCTION site_slugs_on_insert()
    """,
    """
    CREATE TRIGGER site_slugs_rename AFTER UPDATE OF slug ON sites
        FOR EACH ROW WHEN (OLD.slug IS DISTINCT FROM NEW.slug) EXECUTE FUNCTION site_slugs_on_rename()
    """,
)


def upgrade() -> None:
    for statement in (*_TABLES, *_FUNCTIONS, *_TRIGGERS):
        op.execute(statement)


def downgrade() -> None:
    for table, kind in (("groups", "group"), ("sites", "site")):
        op.execute(f"DROP TRIGGER IF EXISTS {kind}_slugs_rename ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {kind}_slugs_insert ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {kind}_slugs_on_rename()")
        op.execute(f"DROP FUNCTION IF EXISTS {kind}_slugs_on_insert()")
    op.execute("DROP TABLE IF EXISTS site_slugs")
    op.execute("DROP TABLE IF EXISTS group_slugs")
