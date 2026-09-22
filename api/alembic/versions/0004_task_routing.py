"""routing metadata on tasks

Revision ID: 0004_task_routing
Revises: 0003_availability
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_task_routing"
down_revision = "0003_availability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("routing", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "routing")
