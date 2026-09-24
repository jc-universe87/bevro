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

    # "url" | "mcp" | "local" | "command"
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # What was typed. May be a path on the worker's machine: never serialised.
    target: Mapped[str] = mapped_column(Text, nullable=False)
    # "pending" (waiting for the worker) | "found" | "failed" | "testing" | "connected"
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the last attempt failed because nothing answered - which is
    # about the process that looked, not about the address. Another of
    # Bevro's processes may sit on a network this one does not.
    unreachable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Last "Test connection" outcome: {"ok": bool, "detail": str | None}
    test: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
