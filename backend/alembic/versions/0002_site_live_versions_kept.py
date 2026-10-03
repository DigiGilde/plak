"""0002_site_live_versions_kept: a retention number per site.

Revision ID: 0002_site_live_versions_kept
Revises: 0001_base
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_site_live_versions_kept"
down_revision = "0001_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sites", sa.Column("live_versions_kept", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_sites_live_versions_kept", "sites", "live_versions_kept >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_sites_live_versions_kept", "sites", type_="check")
    op.drop_column("sites", "live_versions_kept")
