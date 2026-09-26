from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderRef(BaseModel):
    """Who did the work.

    An agent can be removed while its work stays readable, so this may name
    an agent that is no longer here: the id is then absent and `removed` is
    true. The name is the one recorded when the work was done.
    """

    id: uuid.UUID | None = None
    slug: str | None = None
    name: str
    removed: bool = False


class TaskSubmit(BaseModel):
    request: str = Field(min_length=1, max_length=8000)
    # Optional: ask a specific provider (from the Agents screen). Home never sends it.
    provider_id: uuid.UUID | None = None
    # Optional: an approved workspace id, when the user already chose one.
    workspace_id: str | None = Field(default=None, max_length=64)


class WorkspaceRef(BaseModel):
    id: uuid.UUID
    name: str


class FailureAction(BaseModel):
    # "retry" | "add_credential" | "test_connection" | "manage"
    kind: str
    label: str
    # For add_credential: which secret, in words. Never a value.
    secret_name: str | None = None
    secret_label: str | None = None


class FailureOut(BaseModel):
    """Why a run did not finish, in terms a person can act on."""

    category: str  # credential_required | provider_unavailable | execution_failed | configuration_problem
    title: str
    message: str
    actions: list[FailureAction] = Field(default_factory=list)
    # The provider may already have acted on the request, so trying again may
    # send it again. False only when Bevro knows the work never reached it.
    may_repeat: bool = False


class TriedOut(BaseModel):
    """One way Bevro tried to reach the provider, in words. For Details."""

    way: str
    outcome: str


class StatusOut(BaseModel):
    """Where a task is, as a person would put it. One mapping for every page.

    kind:  starting | working | needs_you | completed | failed | stopped
    """

    kind: str
    # A few words, for lists: "Working", "Completed", "Needs a credential".
    label: str
    # One sentence: "Jobs Desk is working on this."
    headline: str
    # Something true worth adding: what the provider says it is doing, that
    # Bevro is waiting for the helper on this machine, that it has not heard
    # anything for a while, that part of a result couldn't be kept.
    note: str | None = None
    # Nothing has been heard for a while. Said, never treated as a failure.
    quiet: bool = False
    # When the task entered this state (created, started or finished).
    since: datetime | None = None
    # Only when Bevro can really stop it.
    can_cancel: bool = False


class RunOut(BaseModel):
    """What the browser may know about a run. No input, no logs, no metadata."""

    id: uuid.UUID
    provider: ProviderRef
    state: str
    result_summary: str | None
    error_summary: str | None
    failure: FailureOut | None = None
    # True when the work succeeded only after Bevro tried another way of
    # reaching the provider. Wording only; never which way.
    recovered: bool = False
    # Short human phase while working, e.g. "Running checks".
    phase: str | None = None
    steps: list[str] = Field(default_factory=list)
    # Every way Bevro tried to reach the provider for this run, in order.
    tried: list[TriedOut] = Field(default_factory=list)
    workspace: WorkspaceRef | None = None
    # Plain sentences, e.g. "Read and modify files in Bevro".
    permissions: list[str] = Field(default_factory=list)
    started_at: datetime | None
    completed_at: datetime | None


class InputRequestOut(BaseModel):
    question: str
    kind: str
    options: list[dict[str, str]] = Field(default_factory=list)


class InputAnswer(BaseModel):
    value: str = Field(min_length=1, max_length=200)


class ArtifactOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    provider_run_id: uuid.UUID | None
    type: str
    title: str
    summary: str | None
    mime_type: str | None
    payload: dict[str, Any] | list[Any] | None
    external_url: str | None
    content_url: str | None
    metadata: dict[str, Any]
    # True when Bevro knows how to render this type itself.
    known: bool
    # The result to show first; the others are "More results".
    primary: bool = False
    created_at: datetime


class TaskOut(BaseModel):
    id: uuid.UUID
    title: str
    original_request: str
    state: str
    summary: str | None
    provider: ProviderRef | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    status: StatusOut
    # How many results it produced, for a list to say so.
    results: int = 0


class TaskDetail(TaskOut):
    runs: list[RunOut]
    artifacts: list[ArtifactOut]
    input_request: InputRequestOut | None = None
