"""agent builds: each attempt at building an agent Bevro was asked to create

Revision ID: 0008_agent_builds
Revises: 0007_bridge_workspaces
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_agent_builds"
down_revision = "0007_bridge_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_builds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("spec", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("spec_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("build_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("state", sa.String(20), nullable=False, server_default="designing"),
        sa.Column("builder", sa.String(80), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("directory", sa.Text, nullable=False, server_default=""),
        sa.Column("validation", postgresql.JSONB, nullable=True),
        sa.Column("source_fingerprint", sa.String(80), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_builds_provider", "agent_builds", ["provider_id"])
    op.create_index("ix_agent_builds_state", "agent_builds", ["state"])


def downgrade() -> None:
    op.drop_index("ix_agent_builds_state", table_name="agent_builds")
    op.drop_index("ix_agent_builds_provider", table_name="agent_builds")
    op.drop_table("agent_builds")
