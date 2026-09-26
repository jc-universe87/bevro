"""Types that cross the Bevro <-> provider boundary."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ProviderSpec(BaseModel):
    """What an adapter is allowed to know about a provider.

    Secrets are resolved separately and handed over per call; they are never
    part of the spec so that a spec can be logged or shown safely.
    """

    id: str
    slug: str
    name: str
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    # {"kind": "local" | "http" | "mcp", "ref": "...", "config": {...}}
    adapter: dict[str, Any] = Field(default_factory=dict)
    app_url: str | None = None

    @property
    def adapter_kind(self) -> str:
        return str(self.adapter.get("kind", ""))

    @property
    def adapter_ref(self) -> str:
        return str(self.adapter.get("ref", ""))

    @property
    def adapter_config(self) -> dict[str, Any]:
        cfg = self.adapter.get("config") or {}
        return dict(cfg)


class InvocationRequest(BaseModel):
    task_id: str
    run_id: str
    request: str
    # Free-form input the router, the user or a previous run attached, e.g.
    # {"workspace_id": "...", "permissions": ["read", "write"]}.
    input: dict[str, Any] = Field(default_factory=dict)
    # Secrets resolved for this call only. Adapters must not persist these.
    secrets: dict[str, str] = Field(default_factory=dict)


class InvocationContext:
    """What a long-running adapter may talk back to while it works.

    `progress(text)` reports a short human phase ("Running checks"). It must
    never be raw output. `cancelled()` is polled by well-behaved adapters.
    `falling_back()` is Bevro's own: the runtime layer calls it when one way
    of reaching a provider failed and the next is being tried. All optional;
    the defaults do nothing.
    """

    def __init__(
        self,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        log_dir: str | None = None,
        fallback: Callable[[], None] | None = None,
    ) -> None:
        self._progress = progress
        self._cancelled = cancelled
        self._fallback = fallback
        # Where an adapter may keep technical logs for debugging. Server-side only.
        self.log_dir = log_dir

    def progress(self, text: str) -> None:
        if self._progress is not None:
            self._progress(text)

    def cancelled(self) -> bool:
        return bool(self._cancelled and self._cancelled())

    def falling_back(self) -> None:
        if self._fallback is not None:
            self._fallback()


class ResultState(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_INPUT = "needs_input"
    NEEDS_APPROVAL = "needs_approval"
    RUNNING = "running"  # accepted but still going; poll with get_status
    CANCELLED = "cancelled"  # stopped on request before it finished


class FailureKind(StrEnum):
    """Why a run did not finish, in terms a person can act on.

    Every runtime adapter translates its native errors into one of these.
    Bevro turns the kind into a sentence and the right actions; the
    technical reason stays server-side. The interface never needs a
    provider-specific error code.
    """

    CONFIGURATION_PROBLEM = "configuration_problem"  # could not be started as configured
    CREDENTIAL_REQUIRED = "credential_required"  # a secret is missing or was rejected
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # could not be reached / is not running
    INVOCATION_FAILED = "invocation_failed"  # started, did not finish
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    OUTPUT_INVALID = "output_invalid"  # answered with something Bevro could not read

    # The older name for invocation_failed; stored values are mapped when read.
    EXECUTION_FAILED = "execution_failed"


class ArtifactDraft(BaseModel):
    """An artifact as a provider proposes it. Bevro decides how to store it."""

    type: str
    title: str
    summary: str | None = None
    mime_type: str | None = None
    payload: dict[str, Any] | list[Any] | None = None
    # Inline file content for Bevro to persist on its own storage.
    content: bytes | None = None
    filename: str | None = None
    external_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvocationResult(BaseModel):
    state: ResultState
    summary: str | None = None
    error: str | None = None
    # For failed results: which kind of failure, so the interface can offer the right next step.
    failure: FailureKind | None = None
    artifacts: list[ArtifactDraft] = Field(default_factory=list)
    external_ref: str | None = None
    # Adapter-specific execution data Bevro keeps on the run (never shown to users).
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthResult(BaseModel):
    ok: bool
    detail: str | None = None
    # Optional finer state: "available", "not_installed", "not_authenticated", "unavailable".
    state: str | None = None
    # Where each credential the provider needs would come from on the machine that
    # ran the check: "bevro" (a stored secret), "host" (the environment), "project"
    # (the provider loads its own), or "missing". Names only, never values.
    credentials: dict[str, str] = Field(default_factory=dict)


class NotSupported(Exception):
    """The adapter (or this provider) does not support the requested operation."""


class InputRequest(BaseModel):
    """A question Bevro must put to the user before a run can start."""

    question: str
    kind: str = "choice"
    options: list[dict[str, str]] = Field(default_factory=list)  # [{"value": "...", "label": "..."}]
    # Which input key the answer fills, e.g. "workspace_id".
    field: str


@runtime_checkable
class ProviderAdapter(Protocol):
    """One transport between Bevro and a family of providers."""

    kind: str
    # "inline": quick, run by the API process itself.
    # "background": may take minutes; a worker process picks it up.
    execution: str = "inline"
    # True when the provider is reached over the network rather than run as a
    # process. Such a provider can be invoked from either the API container or
    # the host worker - whichever can actually reach it - so the worker must be
    # willing to pick one up even though the usual execution is "inline".
    network: bool = False
    # Things Bevro must supply in `input` before a run can start, e.g. {"workspace"}.
    # Bevro asks the user for whatever it cannot infer.
    requires: frozenset[str] = frozenset()

    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult: ...

    def invoke(
        self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None
    ) -> InvocationResult: ...

    def get_status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        """Optional. Raise NotSupported if the transport has no notion of status."""
        ...

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool:
        """Optional. Raise NotSupported if the transport cannot cancel."""
        ...
