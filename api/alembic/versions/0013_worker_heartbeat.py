"""a worker says it is there, instead of being inferred from its work

Whether a worker is running decided whether an address the container cannot
reach is handed to the host at all - and it was inferred from recent
availability reports on background providers. On a fresh installation there
are none, so the one case that needs the worker most was the case that could
not see it.

Revision ID: 0013_worker_heartbeat
Revises: 0012_draft_reachability
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_worker_heartbeat"
down_revision = "0012_draft_reachability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(120), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("kinds", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("network", sa.String(20), nullable=False, server_default="host"),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
