"""provider availability, reported by whoever can actually reach the provider

Revision ID: 0003_availability
Revises: 0002_workspaces
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_availability"
down_revision = "0002_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("availability", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("providers", "availability")
