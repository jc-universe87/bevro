"""AgentSpec: what the person asked for, in a shape Bevro can act on.

Written by a small model (or by local rules when none is configured) from one
sentence. It never contains code, and it is the only description the builder
is given: capabilities come from here, not from whatever the builder decides
to write.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Permission(StrEnum):
    """What an agent will need to be allowed to do. Shown in plain words."""

    WEB = "web"                     # fetch public web pages / search
    FILES_READ = "files_read"       # read files it is given
    FILES_WRITE = "files_write"     # produce files (reports, exports)
    RUN_COMMANDS = "run_commands"   # run programs on this machine
    EXTERNAL_API = "external_api"   # a service that needs a credential
    SCHEDULE = "schedule"           # run on a timetable


PERMISSION_WORDS = {
    Permission.WEB: "Web access",
    Permission.FILES_READ: "Read the files you give it",
    Permission.FILES_WRITE: "Write reports",
    Permission.RUN_COMMANDS: "Run programs on this machine",
    Permission.EXTERNAL_API: "An account you connect",
    Permission.SCHEDULE: "Run on a schedule",
}

OUTPUT_WORDS = {
    "report": "a short report",
    "text": "a written answer",
    "structured": "structured results",
    "file": "a file",
    "summary": "a summary",
}


class SpecCapability(BaseModel):
    id: str = Field(max_length=80)
    title: str = Field(max_length=80)
    description: str | None = Field(default=None, max_length=200)


class AgentSpec(BaseModel):
    """The agreed description of an agent, before anything is built."""

    name: str = Field(max_length=60)
    description: str = Field(max_length=300)
    # One sentence on what the work is for; used in the build request.
    purpose: str = Field(default="", max_length=400)
    capabilities: list[SpecCapability] = Field(default_factory=list, max_length=6)
    # What a request to it will look like, and what it should hand back.
    input_expectation: str = Field(default="A short request in plain words.", max_length=200)
    output_expectation: str = Field(default="text", max_length=200)
    permissions: list[Permission] = Field(default_factory=list)
    # Named services it will need to reach, e.g. ["openai", "github"]. Names only.
    integrations: list[str] = Field(default_factory=list, max_length=6)
    # Plain words if the person clearly asked for recurring work ("every Monday").
    schedule: str | None = Field(default=None, max_length=120)
    # Does it need to remember anything between requests?
    persistence: bool = False
    complexity: str = "small"  # small | medium | large
    # Anything the builder must respect that is not a permission.
    constraints: list[str] = Field(default_factory=list, max_length=8)
    # How this spec was produced: "model" or "rules".
    source: str = "rules"
    version: int = 1

    def preview(self) -> dict[str, Any]:
        """What the person sees before agreeing. No JSON, no jargon."""
        return {
            "name": self.name,
            "description": self.description,
            "can": [c.title for c in self.capabilities],
            "needs": [PERMISSION_WORDS[p] for p in self.permissions],
            "produces": OUTPUT_WORDS.get(self.output_expectation, self.output_expectation),
            "schedule": self.schedule,
        }

    def capability_dicts(self) -> list[dict[str, Any]]:
        return [c.model_dump(exclude_none=True) for c in self.capabilities]


_ID_RE = re.compile(r"[^a-z0-9]+")


def capability(title: str, description: str | None = None) -> SpecCapability:
    cap_id = _ID_RE.sub("_", title.lower()).strip("_")[:80] or "general"
    return SpecCapability(id=cap_id, title=title[:80], description=description)
