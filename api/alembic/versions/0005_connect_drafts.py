"""connect drafts: what discovery found, until the person confirms it

Revision ID: 0005_connect_drafts
Revises: 0004_task_routing
Create Date: 2026-09-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_connect_drafts"
down_revision = "0004_task_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connect_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("draft", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("test", postgresql.JSONB, nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_connect_drafts_state", "connect_drafts", ["state"])


def downgrade() -> None:
    op.drop_index("ix_connect_drafts_state", table_name="connect_drafts")
    op.drop_table("connect_drafts")
