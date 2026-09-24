"""an agent can be removed without taking its work with it

Until now provider_runs.provider_id was NOT NULL with ON DELETE RESTRICT, so a
provider that had ever done anything could not be deleted at all. The record
of the work is the person's, not the provider's, so the link becomes optional
and each run keeps the name of whoever did it.

The backfill runs *before* the constraint changes, so no run can lose the name
of its provider on the way through.

Revision ID: 0011_removable_providers
Revises: 0010_notifications
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_removable_providers"
down_revision = "0010_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Somewhere to keep who did the work.
    op.add_column("provider_runs", sa.Column("provider_name", sa.String(120), nullable=True))
    op.add_column("provider_runs", sa.Column("provider_slug", sa.String(80), nullable=True))

    # 2. Fill it in from the providers that are still here, while they still are.
    op.execute(
        """
        UPDATE provider_runs r
           SET provider_name = p.name, provider_slug = p.slug
          FROM providers p
         WHERE p.id = r.provider_id
        """
    )
    # Any run whose provider had already gone (there should be none, because the
    # old constraint forbade it) still needs a name rather than a blank.
    op.execute("UPDATE provider_runs SET provider_name = 'Removed agent' WHERE provider_name IS NULL")
    op.alter_column("provider_runs", "provider_name", nullable=False, server_default="")

    # 3. Only now let the link go.
    op.drop_constraint("provider_runs_provider_id_fkey", "provider_runs", type_="foreignkey")
    op.alter_column("provider_runs", "provider_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    op.create_foreign_key(
        "provider_runs_provider_id_fkey", "provider_runs", "providers", ["provider_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    # Going back means the old rule applies again, so any run whose provider has
    # since been removed would have nothing to point at. Those rows cannot be
    # rebuilt, so they are removed with their tasks rather than silently broken.
    op.execute("DELETE FROM tasks WHERE id IN (SELECT task_id FROM provider_runs WHERE provider_id IS NULL)")
    op.drop_constraint("provider_runs_provider_id_fkey", "provider_runs", type_="foreignkey")
    op.alter_column("provider_runs", "provider_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)
    op.create_foreign_key(
        "provider_runs_provider_id_fkey", "provider_runs", "providers", ["provider_id"], ["id"], ondelete="RESTRICT"
    )
    op.drop_column("provider_runs", "provider_slug")
    op.drop_column("provider_runs", "provider_name")
