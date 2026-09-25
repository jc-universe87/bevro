"""the ways a person uses an app or agent, apart from Bevro

Knowing something, being able to use it, and Bevro being able to send it
work are three different things (docs/HUB.md). The last is already derived
from runtimes. This keeps the evidence for the second: its own web app, a
chat bot, a schedule it runs on, a command line.

Revision ID: 0019_provider_surfaces
Revises: 0018_connect_by_name
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019_provider_surfaces"
down_revision = "0018_connect_by_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("surfaces", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("providers", "surfaces")
