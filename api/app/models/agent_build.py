"""One attempt at building an agent Bevro was asked to create.

A provider may have several builds over its life; one is active. Keeping them
apart is what lets a rebuild be tried without putting the working version at
risk.
"""

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey


class AgentBuild(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "agent_builds"

    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="CASCADE"), nullable=False)
    # The AgentSpec this build was made from, and which version of it.
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    build_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # "designing" | "building" | "testing" | "ready" | "failed"
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="designing")
    # Which provider built it (a slug), and the task that did the work.
    builder: Mapped[str | None] = mapped_column(String(80), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Where it lives, relative to the managed agents directory. Server-side only.
    directory: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # What validation concluded: {"ok": bool, "reason": str, "runtime": "...", "checks": [...]}
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
