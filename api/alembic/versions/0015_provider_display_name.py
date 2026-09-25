"""a name, without the part that describes its own plumbing

"Ledger control API" is Ledger. "Inventory REST API" is
Inventory. The suffix tells Bevro how to talk to the thing and tells the
person nothing, so it is trimmed for display and the exact name it gave is
kept beside it.

Revision ID: 0015_provider_display_name
Revises: 0014_provider_copy
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_provider_display_name"
down_revision = "0014_provider_copy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("source_name", sa.String(200), nullable=True))
    # Keep the name every connected provider gave before anything trims it.
    op.execute("UPDATE providers SET source_name = name WHERE origin = 'connected'")


def downgrade() -> None:
    op.drop_column("providers", "source_name")
