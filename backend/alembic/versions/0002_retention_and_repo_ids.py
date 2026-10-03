"""0002_retention_and_repo_ids: a retention number per site, and whether a
repository link's ids were ever confirmed.

An existing link counts as confirmed when its latest `site_repository_set`
audit row for the same ids says so; anything else waits for the next
trusted CI token.

Revision ID: 0002_retention_and_repo_ids
Revises: 0001_base
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_retention_and_repo_ids"
down_revision = "0001_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sites", sa.Column("live_versions_kept", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_sites_live_versions_kept", "sites", "live_versions_kept >= 0")

    op.add_column(
        "site_repositories",
        sa.Column("ids_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE site_repositories AS link
        SET ids_confirmed = true
        FROM sites, groups
        WHERE sites.id = link.site_id
          AND groups.id = sites.group_id
          AND (
              SELECT (entry.refs ->> 'ids_confirmed')::boolean
              FROM audit_log_entries AS entry
              WHERE entry.action = 'site_repository_set'
                AND entry.refs ->> 'group' = groups.slug
                AND entry.refs ->> 'site' = sites.slug
                AND entry.refs ->> 'repository_id' = link.repository_id::text
              ORDER BY entry.occurred_at DESC
              LIMIT 1
          )
        """
    )


def downgrade() -> None:
    op.drop_column("site_repositories", "ids_confirmed")
    op.drop_constraint("ck_sites_live_versions_kept", "sites", type_="check")
    op.drop_column("sites", "live_versions_kept")
