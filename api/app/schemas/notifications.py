"""What the browser sees of a notification.

Never where it was sent, never a server's words, never a path. Just: what
happened, why, whether it went out, and where to look.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    id: uuid.UUID
    # "automation.matched" | "automation.finished" | "automation.failed"
    kind: str
    title: str
    summary: str | None = None
    reason: str | None = None
    read: bool
    task_id: uuid.UUID | None = None
    automation_id: uuid.UUID | None = None
    # "in_app" | "sending" | "delivered" | "partly_delivered" | "delivery_failed"
    delivery: str
    # Which ways it was sent: ["in_app", "email"]. Not the addresses.
    channels: list[str] = Field(default_factory=list)
    # Why an external message didn't go, in words the person can act on.
    delivery_problem: str | None = None
    created_at: datetime


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


class ChannelOut(BaseModel):
    """A way of being told, and whether this installation can use it."""

    name: str
    label: str
    available: bool
    external: bool
    # "No mail server is set up on this installation."
    note: str | None = None


class NotifyPreference(BaseModel):
    """How one automation should reach the person."""

    in_app: bool = True
    email: bool = False
    webhook: bool = False
    # Scheduled work only: tell me each time it finishes, not just when
    # something is worth saying.
    on_finish: bool = False
