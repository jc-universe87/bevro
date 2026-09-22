"""initial schema: providers, provider_secrets, tasks, provider_runs, artifacts

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("capabilities", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("adapter", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("app_url", sa.String(2048), nullable=True),
        sa.Column("icon", postgresql.JSONB, nullable=True),
        sa.Column("origin", sa.String(40), nullable=False, server_default="connected"),
        *_timestamps(),
    )
    op.create_table(
        "provider_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("provider_id", "name", name="uq_provider_secret_name"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("original_request", sa.Text, nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="created"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_table(
        "provider_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("input", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("result_summary", sa.Text, nullable=True),
        sa.Column("error_summary", sa.Text, nullable=True),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_provider_runs_task_id", "provider_runs", ["task_id"])
    op.create_index("ix_provider_runs_provider_id", "provider_runs", ["provider_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("provider_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("mime_type", sa.String(120), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("storage_path", sa.String(1024), nullable=True),
        sa.Column("external_url", sa.String(2048), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("provider_runs")
    op.drop_table("tasks")
    op.drop_table("provider_secrets")
    op.drop_table("providers")
