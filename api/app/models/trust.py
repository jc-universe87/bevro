"""A TrustGrant is the person saying yes, once, to one thing on this machine.

Bevro used to decide what it could look at from an environment variable the
person had to edit and restart for: every folder they might ever connect,
listed in advance, in a file, in a container's configuration. That is a
reasonable ceiling for someone hardening a shared installation and a poor way
to add your own project.

So the question moves to where it belongs - the moment of connecting - and
the answer is kept here. A grant is narrow by default: this folder, not its
parent; this program, not everything beside it. Revoking one takes effect the
next time anything is looked at or run, because the worker reads these at use
time rather than trusting a decision made during discovery.

`BEVRO_LOCAL_ROOTS` remains, and means what an administrator would want it to
mean: even a grant the person made must sit inside it.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey, utcnow


class TrustGrant(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "trust_grants"
    __table_args__ = (UniqueConstraint("kind", "target", name="uq_trust_kind_target"),)

    # "folder" - a directory Bevro may inspect and work in
    # "command" - a program Bevro may run
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Canonical and absolute: symlinks resolved before the grant was made, so
    # what was approved cannot change meaning afterwards.
    target: Mapped[str] = mapped_column(Text, nullable=False)
    # "exact"  just this folder and what is under it
    # "tree"   this folder as a parent, chosen deliberately
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="exact")
    # What the person saw when they said yes, for Settings to show later.
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    # "person" (asked in the browser) or "migration" (already connected before
    # Bevro asked anyone anything).
    granted_by: Mapped[str] = mapped_column(String(40), nullable=False, default="person")
    # Kept rather than deleted: what was allowed, and when it stopped being.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def active(self) -> bool:
        return self.revoked_at is None
