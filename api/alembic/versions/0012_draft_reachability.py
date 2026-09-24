"""a draft remembers whether the address simply could not be reached

"Nothing answered" is about the process that looked, not about the address.
The API container and the host worker sit on different networks, so a draft
the API could not reach may still be found by the worker - and that is worth
recording rather than guessing at from an error message.

Revision ID: 0012_draft_reachability
Revises: 0011_removable_providers
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_draft_reachability"
down_revision = "0011_removable_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connect_drafts", sa.Column("unreachable", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("connect_drafts", "unreachable")
