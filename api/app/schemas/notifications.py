"""What the browser sees of a notification.

Never where it was sent, never a server's words, never a path. Just: what
happened, why, whether it went out, and where to look.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
    # Usable with nothing more to type.
    available: bool
    # Worth offering at all: the machinery is here, even if an address is not.
    offerable: bool = False
    external: bool
    # True when a single automation may give an address of its own.
    accepts_destination: bool = False
    # True when the person must supply that address themselves.
    needs_destination: bool = False
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
    # Optional: somewhere of its own. Empty means wherever this installation
    # sends by default. There is no address book; this is one field.
    email_to: str = Field(default="", max_length=320)
    webhook_url: str = Field(default="", max_length=2000)

    @field_validator("email_to")
    @classmethod
    def _plausible_address(cls, value: str) -> str:
        value = (value or "").strip()
        if value and not re.fullmatch(r"[^@\s]+@[^@\s.]+(\.[^@\s.]+)+", value):
            raise ValueError("That doesn't look like an email address.")
        return value

    @field_validator("webhook_url")
    @classmethod
    def _plausible_url(cls, value: str) -> str:
        value = (value or "").strip()
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("A web address must start with http:// or https://.")
        return value
