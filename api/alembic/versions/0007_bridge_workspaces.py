"""workspaces: read-only roots and internal (bridge) workspaces

Revision ID: 0007_bridge_workspaces
Revises: 0006_runtime_profiles
Create Date: 2026-09-22

A coding provider building an integration bridge needs to *read* the project
it is adapting and *write* only into Bevro's own folder for that provider.
`read_paths` expresses the read-only side generically; `internal` keeps a
workspace Bevro made for itself out of the person's project list.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_bridge_workspaces"
down_revision = "0006_runtime_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("read_paths", postgresql.JSONB, nullable=False, server_default="[]"))
    op.add_column("workspaces", sa.Column("internal", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("workspaces", "internal")
    op.drop_column("workspaces", "read_paths")
