"""runtime profiles: how each installation of a provider runs or is reached

Revision ID: 0006_runtime_profiles
Revises: 0005_connect_drafts
Create Date: 2026-09-22

Existing providers are backfilled from their adapter block at API start
(app.services.runtime.ensure_runtimes), so nothing here needs Python models.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_runtime_profiles"
down_revision = "0005_connect_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("runtimes", postgresql.JSONB, nullable=False, server_default="[]"))
    op.add_column("providers", sa.Column("active_runtime", sa.String(40), nullable=True))
    op.add_column("providers", sa.Column("source", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("providers", "source")
    op.drop_column("providers", "active_runtime")
    op.drop_column("providers", "runtimes")
