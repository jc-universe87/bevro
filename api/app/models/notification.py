"""Something worth telling the person about, and the ways it was told.

A Task records the work. A NotificationEvent records that a result mattered.
The two are deliberately separate: a task that succeeded stays successful
however the telling goes, and a message that failed to send can be tried
again without running the work a second time.

Each event is delivered once per channel. The delivery rows carry their own
state, attempts and next try, so several schedulers can share the work and
none of them can send the same thing twice.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey


class NotificationEvent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "notification_events"
    # One logical event per occurrence: two schedulers cannot both raise it.
    __table_args__ = (UniqueConstraint("automation_run_id", name="uq_notification_per_occurrence"),)

    # "automation.matched" | "automation.finished" | "automation.failed".
    # What kind of event it is carries its weight; Bevro has no severity dial.
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="automation.matched")
    automation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("automations.id", ondelete="CASCADE"), nullable=True)
    automation_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("automation_runs.id", ondelete="CASCADE"), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # A few plain sentences. Never artifacts, paths, logs or credentials.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why this was worth saying: "The result is different from last time."
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unread until the person opens it or says they have read it.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deliveries: Mapped[list["NotificationDelivery"]] = relationship(back_populates="event", cascade="all, delete-orphan", lazy="selectin")


class NotificationDelivery(UUIDPrimaryKey, Timestamped, Base):
    """One event on its way out by one channel."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("event_id", "channel", name="uq_delivery_per_channel"),)

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("notification_events.id", ondelete="CASCADE"), nullable=False)
    # "in_app" | "email" | "webhook"
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    # Where it went, for the person's own record. Never returned to the browser.
    destination: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # "pending" | "sending" | "sent" | "failed"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # In words a person can act on. Never a traceback or a server banner.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped[NotificationEvent] = relationship(back_populates="deliveries")
