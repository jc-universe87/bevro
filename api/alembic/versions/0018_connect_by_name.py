"""what a connection attempt was called, and the folders that name could mean

A person may type what something on this machine is called rather than where
it is. When the name fits more than one folder, Bevro asks which; the folders
it found are kept with the attempt (server-side, as the full paths they are)
so that the answer is one of them and nothing else.

Revision ID: 0018_connect_by_name
Revises: 0017_discovery_version
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0018_connect_by_name"
down_revision = "0017_discovery_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connect_drafts", sa.Column("named", sa.String(200), nullable=True))
    op.add_column("connect_drafts", sa.Column("candidates", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("connect_drafts", "candidates")
    op.drop_column("connect_drafts", "named")
