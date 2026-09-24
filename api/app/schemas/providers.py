"""What the browser is allowed to see about providers.

Adapter configuration and secret values never appear here. The connection
method is reported by name only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ConnectionMethod = Literal["api", "mcp", "local", "command"]


class ProviderOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    # One short sentence for the card. Built by app/connect/copy.py, never a
    # service's own account of its interface.
    description: str
    # The plain-English layer behind "More details":
    # {"what_it_does": "...", "how_it_connects": "..."}
    details: dict[str, str] = Field(default_factory=dict)
    enabled: bool
    capabilities: list[dict[str, Any]]
    app_url: str | None
    icon: dict[str, Any] | None
    origin: str
    # e.g. ["ask", "open"] - only actions the provider really supports.
    actions: list[str]
    connection: str | None  # "api" | "mcp" | "local" | "built-in" | "declared"
    # {"state": "available" | "not_installed" | "not_authenticated" | "unavailable", "note": "Not available on this installation" | None}
    availability: dict[str, str | None] = Field(default_factory=dict)
    secret_names: list[str] = Field(default_factory=list)
    # What the connection needs and whether it has it: [{"name": "OPENAI_API_KEY", "label": "OpenAI credential", "present": false}]
    credentials: list[dict[str, Any]] = Field(default_factory=list)
    # How this installation runs, in words: {"display_name": "Already running on this machine",
    # "credentials_label": "Managed by provider", "runtimes_found": 2, "built": false}.
    # Mechanism stays server-side.
    runtime: dict[str, Any] | None = None
    # For agents Bevro created: {"purpose": ..., "version": 3, "state": "ready"}.
    # Never source code or paths.
    build: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ProviderDetails(BaseModel):
    """Advanced details: the technical account, for when something is wrong.

    Kinds and mechanisms by name, and the address the person themselves
    typed, given back to them. Never a path (which describes this machine),
    never a command (whose arguments may carry a secret), never a secret.
    """

    id: uuid.UUID
    active_runtime: dict[str, Any] | None
    runtimes: list[dict[str, Any]] = Field(default_factory=list)
    source_kind: str | None = None
    # The exact name the thing gave, when Bevro shows a shorter one.
    source_name: str | None = None
    # The address that was typed into Connect, credentials stripped. Never a
    # folder (a path describes this machine) and never a command.
    source_target: str | None = None
    # What the service said about itself when Bevro found it. Kept whole, and
    # shown only here - it is a fact about the service, not copy for a card.
    source_description: str | None = None
    # For a service whose work is typed operations: how many Bevro can use.
    operation_count: int | None = None
    # "This machine" when only the worker can reach it, else "Bevro itself".
    runs_at: str | None = None
    # {"api": "unavailable", "worker": "available", ...} - who has reached it.
    reachability: dict[str, Any] | None = None


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


class ProviderConnect(BaseModel):
    """Advanced setup: the escape hatch when discovery cannot work something out.

    details, by method:
      api      base_url, request_template ("POST /ask {"query": "{request}"}"), response_field
      mcp      server_url or command (+ working_directory), tool ("name" or "name:argument")
      local    base_url (a service on this machine or network; same as api)
      command  command, working_directory, input_flag ("--topic"; empty = last argument),
               secret_env (name of the environment variable a secret is passed as)
    """

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    capabilities: list[str | dict[str, Any]] = Field(default_factory=list)
    method: ConnectionMethod
    # Method-specific, non-secret details: base_url, server_url, command ...
    details: dict[str, Any] = Field(default_factory=dict)
    # Secret values, e.g. {"api_key": "..."}. Stored encrypted; never returned.
    secrets: dict[str, str] = Field(default_factory=dict)
    app_url: str | None = None

    @field_validator("capabilities")
    @classmethod
    def _normalise(cls, value: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    out.append({"id": text.lower().replace(" ", "_"), "title": text})
            elif item.get("id"):
                out.append(item)
        return out

    @field_validator("app_url")
    @classmethod
    def _blank_url(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class ProviderUpdate(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class HealthOut(BaseModel):
    ok: bool
    detail: str | None = None


class CreatePreviewIn(BaseModel):
    description: str = Field(min_length=3, max_length=4000)


class RemovalPlanOut(BaseModel):
    """What removing an agent would take with it, so the question can be honest."""

    # Built-in agents stay; everything else can go.
    removable: bool = True
    # How many pieces of work it did. These are kept, and said so.
    history: int = 0
    # Work running right now, which must finish or be cancelled first.
    in_flight: int = 0
    credentials: int = 0
    # A project or a connection Bevro generated and owns. These do go.
    built_project: bool = False
    built_connection: bool = False


class CreatePreviewOut(BaseModel):
    """What the person is shown before agreeing. Plain words, never the spec itself."""

    name: str
    description: str
    # ["Research", "Reports"] - what it will be able to do.
    can: list[str] = Field(default_factory=list)
    # ["Web access", "Write reports"] - what it will need.
    needs: list[str] = Field(default_factory=list)
    produces: str = "a written answer"
    schedule: str | None = None
    # True when a connected agent can actually build it.
    can_build: bool = True
    # The agreed description, handed back on Create so nothing is re-derived.
    spec: dict[str, Any] = Field(default_factory=dict)
    # Older shape, still filled in so nothing that used it breaks.
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True


class CreateBuildIn(BaseModel):
    """Create the agent that was previewed."""

    spec: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=4000)


class CreateStatusOut(BaseModel):
    state: str  # designing | building | testing | connecting | ready | failed
    note: str
    provider_id: uuid.UUID
    task_id: uuid.UUID | None = None
    steps: list[str] = Field(default_factory=list)
    detail: str | None = None


class CreateEditIn(BaseModel):
    """Change what an agent is for, and build it again.

    An empty description means "build the same agent again", which is what
    Rebuild does when the purpose has not been edited.
    """

    description: str = Field(default="", max_length=4000)


class CreateActivateIn(BaseModel):
    """Older shape: register a described agent without building it."""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True
    source_description: str = Field(default="", max_length=4000)
