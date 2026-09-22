"""notifications: what mattered, and how the person was told

Revision ID: 0010_notifications
Revises: 0009_automations
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_notifications"
down_revision = "0009_automations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automations", sa.Column("notify", postgresql.JSONB, nullable=True))
    op.create_table(
        "notification_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False, server_default="automation.matched"),
        sa.Column("automation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("automations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("automation_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("automation_runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # One event per occurrence: the database, not the scheduler, guarantees it.
    op.create_unique_constraint("uq_notification_per_occurrence", "notification_events", ["automation_run_id"])
    op.create_index("ix_notification_events_unread", "notification_events", ["read_at", "created_at"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notification_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("destination", sa.String(320), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_delivery_per_channel", "notification_deliveries", ["event_id", "channel"])
    op.create_index("ix_notification_deliveries_due", "notification_deliveries", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_due", table_name="notification_deliveries")
    op.drop_constraint("uq_delivery_per_channel", "notification_deliveries", type_="unique")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notification_events_unread", table_name="notification_events")
    op.drop_constraint("uq_notification_per_occurrence", "notification_events", type_="unique")
    op.drop_table("notification_events")
    op.drop_column("automations", "notify")
