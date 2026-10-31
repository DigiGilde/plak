"""0007_drop_storage_ref: a version's place follows from its ids.

Since 0003 every version lies at {site_id}/{version_id} on the content
volume, so versions.storage_ref only repeated the two ids on its own row.
Only the database changes: the volume stays as it is.

Revision ID: 0007_drop_storage_ref
Revises: 0006_versions_and_keys_indexes
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_drop_storage_ref"
down_revision = "0006_versions_and_keys_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("versions", "storage_ref")


def downgrade() -> None:
    op.add_column("versions", sa.Column("storage_ref", sa.String(), nullable=True))
    op.execute("UPDATE versions SET storage_ref = site_id::text || '/' || id::text")
    op.alter_column("versions", "storage_ref", nullable=False)
