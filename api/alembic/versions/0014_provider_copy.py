"""what a service says about itself, kept apart from what a person reads

A service's own description is written for whoever integrates with it. Shown
on a card it is a wall of implementation detail, and it was the only thing
Bevro had, so it was what people saw. It moves to its own column, where
Advanced details can show it and the card no longer has to.

Nothing is lost: every existing description is copied across first.

Revision ID: 0014_provider_copy
Revises: 0013_worker_heartbeat
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_provider_copy"
down_revision = "0013_worker_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("source_description", sa.Text, nullable=True))
    # Keep what every connected provider was found saying. Built-ins and
    # agents Bevro wrote already carry copy meant for a person, so they have
    # nothing to preserve and keep their description as it stands.
    op.execute(
        """
        UPDATE providers
           SET source_description = description
         WHERE origin = 'connected'
           AND description IS NOT NULL
           AND description <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("providers", "source_description")
