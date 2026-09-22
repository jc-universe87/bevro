"""A Task belongs to Bevro, not to any provider.

It records what was asked, where it has got to, and the concise outcome.
The provider work that happened along the way is in ProviderRun rows.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.task_state import TaskState
from app.models._common import Timestamped, UUIDPrimaryKey


class Task(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    original_request: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskState.CREATED)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How the provider was chosen: {"source", "router_version", "backend", "selected_provider_ids",
    # "confidence", "rationale", "plan", "fallback_reason"}. Internal; never sent to the browser.
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    runs: Mapped[list["ProviderRun"]] = relationship(  # noqa: F821
        back_populates="task", cascade="all, delete-orphan", order_by="ProviderRun.created_at"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(  # noqa: F821
        back_populates="task", cascade="all, delete-orphan", order_by="Artifact.created_at"
    )
