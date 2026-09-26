"""What Home is told about a request before anything runs."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RouteRequest(BaseModel):
    request: str = Field(min_length=1, max_length=8000)
    # The person picked this item from a choice: answer for it alone.
    provider_id: uuid.UUID | None = None


class RouteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class RouteChoice(RouteItem):
    summary: str


class RouteAnswer(BaseModel):
    """Plain sentences and names only: no scores, no mechanics."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["direct", "handoff", "blocked", "unavailable", "how_to", "setup", "choice", "none"]
    message: str
    # Sure enough to act without asking. Only ever a yes or no.
    sure: bool
    item: RouteItem | None = None
    why: str | None = None
    choices: list[RouteChoice] = Field(default_factory=list)
