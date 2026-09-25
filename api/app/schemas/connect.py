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
    # "looking" | "trust_required" | "found" | "failed" | "testing" | "connected"
    state: str
    target_kind: str
    target_label: str
    draft: dict[str, Any] | None = None
    # When state is "trust_required": what Bevro is asking about, in the words
    # the question needs - including the resolved path, so that what is agreed
    # to is what will be used.
    trust: dict[str, Any] | None = None
    # When state is "choice_required": the folders a name could mean, as
    # [{"label": "research-agent", "where": "~/agents/research-agent"}]. The
    # answer is a position in this list, never a path.
    choices: list[dict[str, str]] | None = None
    # True when Bevro found this by what it is called, on this machine.
    found_by_name: bool = False
    error: str | None = None
    # When state is "failed": what kind of failure, so the page can offer the
    # way out that fits - "worker" (this machine can't be reached right now),
    # "unreachable", "not_found" or "failed". The words are in `error`.
    problem: str | None = None
    # When what was found is already connected: {"id", "name"}. The page then
    # points at it instead of offering a second copy.
    already_connected: dict[str, Any] | None = None
    # After a test: {"ok", "detail", "checks": [{"label", "ok", "kind"?}], "next"}.
    # `ok` in a check is true, false, or null for "not checked, and why".
    test: dict[str, Any] | None = None
    provider_id: uuid.UUID | None = None
    created_at: datetime


class DescribeIn(BaseModel):
    # What it is for, in the person's words: "search documents, create reports".
    capability_summary: str = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ChooseIn(BaseModel):
    choice: int = Field(ge=0, le=20)


class TrustIn(BaseModel):
    # "exact" this folder; "parent" the folder above it, chosen deliberately.
    scope: str = Field(default="exact", pattern="^(exact|parent|tree)$")


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
    # When a scoped service has several and asked: which profile, workspace
    # or tenant this connection is for.
    scope: str | None = Field(default=None, max_length=120)
