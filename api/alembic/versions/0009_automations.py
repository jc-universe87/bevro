"""automations: work Bevro repeats, and what each occurrence found

Revision ID: 0009_automations
Revises: 0008_agent_builds
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_automations"
down_revision = "0008_agent_builds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("instruction", sa.Text, nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("selection", sa.String(20), nullable=False, server_default="dynamic"),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("schedule", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("condition", postgresql.JSONB, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_digest", sa.String(80), nullable=True),
        sa.Column("last_summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_automations_due", "automations", ["enabled", "next_run_at"])
    op.create_table(
        "automation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("automation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("automations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delayed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="schedule"),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("matched", sa.Boolean, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("surfaced", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_automation_runs_automation", "automation_runs", ["automation_id"])
    op.create_unique_constraint("uq_automation_occurrence", "automation_runs", ["automation_id", "scheduled_for"])


def downgrade() -> None:
    op.drop_constraint("uq_automation_occurrence", "automation_runs", type_="unique")
    op.drop_index("ix_automation_runs_automation", table_name="automation_runs")
    op.drop_table("automation_runs")
    op.drop_index("ix_automations_due", table_name="automations")
    op.drop_table("automations")
