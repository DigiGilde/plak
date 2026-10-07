"""0003_versions_and_keys_indexes: index the site of a version and of a
secret link.

PostgreSQL does not index a foreign key column by itself. The version list,
the retention job and the overview read versions per site, newest first;
the secret links tab reads access keys per site.

Revision ID: 0003_versions_and_keys_indexes
Revises: 0002_retention_and_repo_ids
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_versions_and_keys_indexes"
down_revision = "0002_retention_and_repo_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_versions_site_id_created_at", "versions", ["site_id", sa.text("created_at DESC")])
    op.create_index("ix_access_keys_site_id", "access_keys", ["site_id"])


def downgrade() -> None:
    op.drop_index("ix_access_keys_site_id", table_name="access_keys")
    op.drop_index("ix_versions_site_id_created_at", table_name="versions")
