"""What the browser sends to and receives from Connect. No adapter blocks, no paths."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DiscoverIn(BaseModel):
    target: str = Field(min_length=1, max_length=2000)
    # Credentials typed for probing (e.g. {"api_key": "..."}); used for the probe only.
    secrets: dict[str, str] = Field(default_factory=dict)


class DraftOut(BaseModel):
    id: uuid.UUID
    # "looking" | "found" | "failed" | "testing" | "connected"
    state: str
    target_kind: str
    target_label: str
    draft: dict[str, Any] | None = None
    error: str | None = None
    test: dict[str, Any] | None = None
    provider_id: uuid.UUID | None = None
    created_at: datetime


class BridgeStatusOut(BaseModel):
    """Where building a connection has got to, in plain words."""

    state: str  # preparing | building | testing | ready | failed
    note: str
    provider_id: uuid.UUID
    task_id: uuid.UUID | None = None
    steps: list[str] = Field(default_factory=list)


class TestIn(BaseModel):
    secrets: dict[str, str] = Field(default_factory=dict)


class ConfirmIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    # A plain, comma-separated summary the person may have edited: "Research, Product strategy"
    capability_summary: str | None = Field(default=None, max_length=1000)
    secrets: dict[str, str] = Field(default_factory=dict)
    app_url: str | None = Field(default=None, max_length=2048)
    enabled: bool = True
    # When discovery found more than one way and asked: the chosen runtime's id.
    runtime_id: str | None = Field(default=None, max_length=40)
