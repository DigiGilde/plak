"""0004_site_id_required: whether a repository link requires a CI token bound
to its site.

A link made from now on requires it (the column default): the workflow names
the site id, and the token's audience is PLAK_BASE_URL/-/sites/{site id}.
Of the links that exist already, the link of a repository linked to that
one site keeps accepting a token whose audience is PLAK_BASE_URL itself,
until another repository is linked or a site admin requires the site id.
A repository linked to several sites requires it on every link at once: one
of them may be someone else's, made from their own site to the repository
(a typo of an address, say), where a workflow without a site id that names
that address would publish. The repository is (provider, host,
repository_id), as on the relink in api/admin.py.

A trigger keeps the rule in the database itself, so also for code that
knows nothing of the column, such as the image before 0004 after a
rollback: an update never sets a required site id back to false, and an
update that links another repository requires it.

The downgrade refuses while any link requires the site id: an upgrade after
it would exempt every link of a repository linked to one site again, closed
or made since, and so silently reopen what was closed. An image rollback
needs no downgrade: the code before 0004 ignores the column, and a link it
makes gets the default, true.

Revision ID: 0004_site_id_required
Revises: 0003_storage_by_site_id
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_site_id_required"
down_revision = "0003_storage_by_site_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "site_repositories",
        sa.Column("site_id_required", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute(
        """
        UPDATE site_repositories AS link
        SET site_id_required = false
        WHERE (
            SELECT count(*) FROM site_repositories AS other
            WHERE other.provider = link.provider
              AND other.host = link.host
              AND other.repository_id = link.repository_id
        ) = 1
        """
    )
    op.execute(
        """
        CREATE FUNCTION site_repositories_site_id_required() RETURNS trigger AS $$
        BEGIN
            NEW.site_id_required := OLD.site_id_required OR NEW.site_id_required
                OR (OLD.provider, OLD.host, OLD.repository_id)
                    IS DISTINCT FROM (NEW.provider, NEW.host, NEW.repository_id);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER site_repositories_site_id_required
        BEFORE UPDATE ON site_repositories
        FOR EACH ROW EXECUTE FUNCTION site_repositories_site_id_required()
        """
    )


def downgrade() -> None:
    # Held to the commit: no link can come to require the site id between the
    # count and dropping the column.
    op.execute("LOCK TABLE site_repositories IN SHARE MODE")
    required = op.get_bind().scalar(sa.text("SELECT count(*) FROM site_repositories WHERE site_id_required"))
    if required:
        raise RuntimeError(
            f"Refusing to downgrade while repository links require a site id ({required} with "
            "site_id_required): upgrading again would let links that now require it publish without one "
            "again. Roll back the image without this downgrade; the code before 0004 ignores the column."
        )
    op.execute("DROP TRIGGER IF EXISTS site_repositories_site_id_required ON site_repositories")
    op.execute("DROP FUNCTION IF EXISTS site_repositories_site_id_required()")
    op.drop_column("site_repositories", "site_id_required")
