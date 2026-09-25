"""A ConnectDraft is what discovery produced for one Connect attempt.

It carries the full ProviderDraft (server-side, including paths) until the
person confirms it. The browser only ever receives the draft's public view.
"""

import uuid
from typing import Any

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey


class ConnectDraft(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "connect_drafts"

    # "url" | "mcp" | "local" | "command" | "name" (until the worker says
    # which folder the name means; then "local")
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # What was typed. May be a path on the worker's machine: never serialised.
    target: Mapped[str] = mapped_column(Text, nullable=False)
    # "choice_required" (a name that fits more than one folder) |
    # "pending" (waiting for the worker) | "trust_required" (waiting for the
    # person) | "found" | "failed" | "testing" | "connected"
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the last attempt failed because nothing answered - which is
    # about the process that looked, not about the address. Another of
    # Bevro's processes may sit on a network this one does not.
    unreachable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # What Bevro needs permission for before it looks: the resolved path or
    # the program, and what it would be allowed to do. Cleared once granted.
    trust: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # The name the person typed, when Bevro found the thing by what it is
    # called rather than by where it is.
    named: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # When a name fits more than one folder: [{"path", "label", "where"}].
    # The paths stay here; the person picks one by its position.
    candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # Last "Test connection" outcome: {"ok": bool, "detail": str | None}
    test: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
