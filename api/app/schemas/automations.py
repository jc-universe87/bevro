"""What the browser sees of repeating work. Plain words; no cron, no internals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.notifications import NotifyPreference
from app.schemas.tasks import ProviderRef


class AutomationOut(BaseModel):
    id: uuid.UUID
    title: str
    instruction: str
    # "scheduled" | "monitoring"
    mode: str
    enabled: bool
    # "Every Monday · 09:00"
    schedule: str
    # Monitoring only: "Notify when the result changes"
    condition: str | None = None
    # The agent it always uses, when it was set up from one piece of work.
    provider: ProviderRef | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    # "No change last run" / "Ran on Monday" / "Didn't run: Research wasn't available"
    last_result: str | None = None
    # How the person wants to hear about this one.
    notify: NotifyPreference = Field(default_factory=NotifyPreference)
    created_at: datetime


class AutomationDetail(AutomationOut):
    runs: list[dict[str, Any]] = Field(default_factory=list)
    # Advanced details only.
    recurrence: str | None = None
    timezone: str | None = None
    selection: str | None = None


class ScheduleIn(BaseModel):
    """Set work up to happen again, in the person's own words."""

    # "Every Monday morning" - or the whole sentence, for Home.
    when: str = Field(min_length=2, max_length=400)
    # What to ask. Omitted when scheduling an existing task.
    instruction: str | None = Field(default=None, max_length=8000)
    task_id: uuid.UUID | None = None
    # "Only tell me when something changes" - the words, not a rule.
    only_when: str | None = Field(default=None, max_length=400)
    # How to be told. Omitted means: in Bevro, and nowhere else.
    notify: NotifyPreference | None = None
    timezone: str = Field(default="UTC", max_length=60)


class AutomationUpdate(BaseModel):
    when: str | None = Field(default=None, max_length=400)
    instruction: str | None = Field(default=None, max_length=8000)
    only_when: str | None = Field(default=None, max_length=400)
    enabled: bool | None = None
    notify: NotifyPreference | None = None
    timezone: str = Field(default="UTC", max_length=60)


class IntentIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    timezone: str = Field(default="UTC", max_length=60)


class IntentOut(BaseModel):
    """What Bevro would set up, for the person to agree to first."""

    recurring: bool
    title: str | None = None
    instruction: str | None = None
    schedule: str | None = None
    condition: str | None = None
    mode: str | None = None
