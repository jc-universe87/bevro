"""An Automation is Bevro's own record of work that should happen again.

Nothing about it belongs to a provider: the instruction, the recurrence, the
condition and the history are Bevro's. A provider is asked the same way it
would be asked by a person, at the moment Bevro decides.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey


class Automation(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "automations"

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    # What Bevro asks, in the person's words. Exactly what a person would type.
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    # "scheduled": every run is the point. "monitoring": only surface when the
    # condition is met.
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    # "pinned": always this provider. "dynamic": the router chooses each time.
    selection: Mapped[str] = mapped_column(String(20), nullable=False, default="dynamic")
    provider_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True)
    # A ScheduleSpec and, for monitoring, a ConditionSpec.
    schedule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    condition: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The fingerprint of the last result, so "has it changed" has an answer.
    last_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # A short summary of the last result, for the next comparison. Never artifacts.
    last_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How the person wants to hear about this one: {"in_app", "email",
    # "webhook", "on_finish"}. Empty means Bevro's defaults.
    notify: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    provider = relationship("Provider")
    runs: Mapped[list["AutomationRun"]] = relationship(back_populates="automation", cascade="all, delete-orphan")


class AutomationRun(UUIDPrimaryKey, Timestamped, Base):
    """One occurrence. The Task remains the authoritative record of the work."""

    __tablename__ = "automation_runs"
    # One occurrence per automation: two schedulers cannot both create it.
    __table_args__ = (UniqueConstraint("automation_id", "scheduled_for", name="uq_automation_occurrence"),)

    automation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("automations.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    # When it was due, and when it actually began.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # True when Bevro was away and this stands in for one or more missed turns.
    delayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "schedule" | "run_now" | "catch_up"
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="schedule")
    # "completed" | "failed" | "running"
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Monitoring: was it worth telling anyone, and why.
    matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True once it has been surfaced to the person.
    surfaced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    automation: Mapped[Automation] = relationship(back_populates="runs")
