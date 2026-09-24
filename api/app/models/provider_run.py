"""One invocation of one provider on behalf of a task.

Keeping this separate from Task is the architectural point: a task may
involve several providers over its life, in sequence or in parallel.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.run_state import RunState
from app.models._common import Timestamped, UUIDPrimaryKey


class ProviderRun(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "provider_runs"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # An agent can be removed while the work it did stays readable, so the link
    # is allowed to go: the database nullifies it and the two columns below
    # carry what a person needs to still recognise the run.
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Who did this work, recorded when it ran. Kept verbatim afterwards: this
    # is history, so it must not change when a provider is renamed or removed.
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    provider_slug: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=RunState.PENDING)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opaque handle the provider gave us, for get_status / cancel later.
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "inline": executed by the API process. "background": picked up by a worker.
    execution: Mapped[str] = mapped_column(String(20), nullable=False, default="inline")
    # Human-readable progress: {"phase": "Running checks", "steps": ["Inspecting the project", ...]}
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # A question Bevro needs answered before the run can go on:
    # {"question": "...", "kind": "choice", "options": [{"value": "...", "label": "..."}]}
    input_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Adapter-specific execution data (session ids, cost, git state...). Never sent to the browser.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    task: Mapped["Task"] = relationship(back_populates="runs")  # noqa: F821
    # None once the agent has been removed; provider_name still says who it was.
    provider: Mapped["Provider | None"] = relationship(back_populates="runs")  # noqa: F821
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="provider_run")  # noqa: F821
