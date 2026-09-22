"""workspaces registry; background execution fields on provider_runs

Revision ID: 0002_workspaces
Revises: 0001_initial
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_workspaces"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("permissions", postgresql.JSONB, nullable=False, server_default='["read"]'),
        sa.Column("repository", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("provider_runs", sa.Column("progress", postgresql.JSONB, nullable=True))
    op.add_column("provider_runs", sa.Column("input_request", postgresql.JSONB, nullable=True))
    op.add_column("provider_runs", sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("provider_runs", sa.Column("worker_id", sa.String(120), nullable=True))
    op.add_column("provider_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("provider_runs", sa.Column("execution", sa.String(20), nullable=False, server_default="inline"))
    op.add_column("provider_runs", sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"))
    op.create_index("ix_provider_runs_state", "provider_runs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_provider_runs_state", table_name="provider_runs")
    for col in ("meta", "execution", "heartbeat_at", "worker_id", "cancel_requested", "input_request", "progress"):
        op.drop_column("provider_runs", col)
    op.drop_table("workspaces")
