"""which version of discovery gathered a provider's evidence

Bevro keeps learning how to look at things - that a systemd unit says where
its credentials come from, that a folder needs permission, that an address
may only be reachable from the host. A provider connected before one of those
went on saying what the older Bevro concluded.

Recording which version found it lets the worker look again, once, instead of
the person being asked to remove and re-add everything they own.

Revision ID: 0017_discovery_version
Revises: 0016_trust_grants
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_discovery_version"
down_revision = "0016_trust_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("discovery_version", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("providers", "discovery_version")
