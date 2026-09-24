"""the person says yes once, instead of editing a file and restarting

Which folders Bevro could look at came from an environment variable listing
every one of them in advance. That is a sensible ceiling for a hardened
installation and a poor way to add your own project, so the question moves to
the moment of connecting and the answer is kept here.

BEVRO_LOCAL_ROOTS keeps working and changes meaning: not the list of folders
that may be used, but the boundary a person's own grants must stay inside.

Revision ID: 0016_trust_grants
Revises: 0015_provider_display_name
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_trust_grants"
down_revision = "0015_provider_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trust_grants",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("scope", sa.String(10), nullable=False, server_default="exact"),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("granted_by", sa.String(40), nullable=False, server_default="person"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "target", name="uq_trust_kind_target"),
    )
    op.add_column("connect_drafts", sa.Column("trust", sa.dialects.postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("connect_drafts", "trust")
    op.drop_table("trust_grants")
