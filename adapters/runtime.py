"""RuntimeProfile: how one installation of a provider actually runs or is reached.

    Provider     what can do work (name, description, capabilities)
    Runtime      how *this* installation runs or is reached: an HTTP service that
                 is already up, a command in a folder, an MCP server, a Compose
                 service, a systemd unit...
    Adapter      Bevro's implementation of one runtime mechanism (adapters/http.py,
                 adapters/command.py, adapters/mcp.py, ...)
    ProviderRun  one execution of work, through the provider's active runtime

A provider may have several runtime profiles; one is active. Everything here is
provider-neutral: no field knows or cares which agent it describes. The
technical parts (`adapter`, `target`) stay server-side; `public()` is what a
browser may see.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from adapters.base import ArtifactDraft, FailureKind, HealthResult, InvocationContext, InvocationRequest, InvocationResult, ProviderSpec


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeKind(StrEnum):
    HTTP = "http"                        # a web service with its own API (already up, or startable)
    MCP_HTTP = "mcp_http"                # MCP over Streamable HTTP
    MCP_STDIO = "mcp_stdio"              # MCP over a subprocess
    CLI = "cli"                          # a command that takes a request and prints/writes a result
    DOCKER_COMPOSE = "docker_compose"    # a Compose service (reached over HTTP, or run as a one-off)
    SYSTEMD = "systemd"                  # a systemd-managed unit (reached over HTTP when it exposes one)
    PROCESS = "process"                  # something already running that Bevro found
    PYTHON_ENTRYPOINT = "python_entrypoint"
    NODE_ENTRYPOINT = "node_entrypoint"
    FILE_EXCHANGE = "file_exchange"      # request in a file, result files out (not run by Bevro)
    BUILTIN = "builtin"                  # shipped with Bevro (example providers, the coding tool)
    UNKNOWN = "unknown"


# Which adapter (Bevro's implementation) drives each runtime kind. Data, not code:
# a new runtime kind is a new entry here plus an adapter that implements the contract.
ADAPTER_FOR_KIND: dict[str, str] = {
    RuntimeKind.HTTP: "http",
    RuntimeKind.MCP_HTTP: "mcp",
    RuntimeKind.MCP_STDIO: "mcp",
    RuntimeKind.CLI: "command",
    RuntimeKind.DOCKER_COMPOSE: "http",      # a compose service is reached over HTTP; one-offs set adapter.kind = "command"
    RuntimeKind.SYSTEMD: "http",
    RuntimeKind.PROCESS: "http",
    RuntimeKind.PYTHON_ENTRYPOINT: "command",
    RuntimeKind.NODE_ENTRYPOINT: "command",
    RuntimeKind.FILE_EXCHANGE: "command",
    RuntimeKind.BUILTIN: "local",
    RuntimeKind.UNKNOWN: "",
}


class CredentialStrategy(StrEnum):
    """How this runtime normally gets the secrets it needs."""

    RUNTIME_MANAGED = "runtime_managed"          # a remote service holds its own
    INHERITED_ENVIRONMENT = "inherited_environment"  # the worker's environment carries it
    PROJECT_DOTENV = "project_dotenv"            # the project loads its own .env
    DOCKER_ENVIRONMENT = "docker_environment"    # compose environment / env_file
    SYSTEMD_ENVIRONMENT = "systemd_environment"  # a unit's EnvironmentFile
    EXTERNAL_SECRET_STORE = "external_secret_store"
    BEVRO_MANAGED = "bevro_managed"              # Bevro stores it encrypted and injects it
    NONE = "none"
    UNKNOWN = "unknown"


# Strategies where the provider or its runtime already looks after the secret:
# Bevro must neither read it nor ask for it.
NATIVE_STRATEGIES = frozenset(
    {CredentialStrategy.RUNTIME_MANAGED, CredentialStrategy.PROJECT_DOTENV, CredentialStrategy.DOCKER_ENVIRONMENT, CredentialStrategy.SYSTEMD_ENVIRONMENT, CredentialStrategy.EXTERNAL_SECRET_STORE}
)


class Credentials(BaseModel):
    strategy: CredentialStrategy = CredentialStrategy.NONE
    # Secret names the runtime needs (environment variable names, or "api_key"). Names only.
    names: list[str] = Field(default_factory=list)
    # True only when the runtime cannot get the secret any other way and Bevro can inject it.
    required_from_user: bool = False
    note: str | None = None  # one plain sentence for the person


class InputMode(StrEnum):
    ARGUMENT = "argument"        # last command-line argument
    FLAG = "flag"                # --topic "<request>"
    STDIN = "stdin"
    HTTP_JSON = "http_json"      # a JSON body field
    MCP_ARGUMENT = "mcp_argument"
    FILE = "file"                # written to a file whose path is passed
    ENVIRONMENT = "environment"  # an environment variable
    QUEUE = "queue"              # not run by Bevro yet
    NONE = "none"


class OutputMode(StrEnum):
    STDOUT = "stdout"
    JSON_RESPONSE = "json_response"
    FILES = "files"              # files the program names in its output
    REPORT_DIR = "report_dir"    # new files under a directory
    HTTP_RESULT = "http_result"  # the Bevro HTTP contract
    MCP_RESULT = "mcp_result"
    DEEP_LINK = "deep_link"


class RuntimeAbilities(BaseModel):
    """What the runtime can do for Bevro. Distinct from provider capabilities."""

    accepts_prompt: bool = True   # can take an arbitrary request
    background: bool = False      # may take minutes; runs through the worker
    cancel: bool = False
    status: bool = False
    streaming: bool = False       # progress phases while it works
    file_artifacts: bool = False
    health: bool = True


class HealthState(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"      # one recent failure; still eligible, ranked lower
    UNAVAILABLE = "unavailable"  # repeatedly failed; skipped while in cooldown
    UNKNOWN = "unknown"


# How long a runtime is passed over after consecutive failures. Short on
# purpose: a transient failure must never take a runtime out for long.
COOLDOWN_SECONDS = (60, 300, 900)
MAX_COOLDOWN_SECONDS = COOLDOWN_SECONDS[-1]


class RuntimeHealth(BaseModel):
    """What recent experience says about a runtime. Provider-neutral."""

    state: HealthState = HealthState.UNKNOWN
    last_checked_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    # Until this moment the runtime is passed over in favour of a healthy one.
    cooldown_until: datetime | None = None
    # One short sentence, server-side only.
    detail: str | None = None

    def in_cooldown(self, now: datetime | None = None) -> bool:
        if self.cooldown_until is None:
            return False
        return (now or _utcnow()) < self.cooldown_until

    def after_success(self, now: datetime | None = None) -> "RuntimeHealth":
        moment = now or _utcnow()
        return RuntimeHealth(state=HealthState.AVAILABLE, last_checked_at=moment, last_failure_at=self.last_failure_at, consecutive_failures=0, cooldown_until=None)

    def after_failure(self, detail: str | None = None, now: datetime | None = None) -> "RuntimeHealth":
        moment = now or _utcnow()
        failures = self.consecutive_failures + 1
        seconds = COOLDOWN_SECONDS[min(failures, len(COOLDOWN_SECONDS)) - 1]
        state = HealthState.DEGRADED if failures == 1 else HealthState.UNAVAILABLE
        return RuntimeHealth(
            state=state,
            last_checked_at=moment,
            last_failure_at=moment,
            consecutive_failures=failures,
            cooldown_until=moment + timedelta(seconds=seconds),
            detail=(detail or "")[:200] or None,
        )


class RuntimeAttempt(BaseModel):
    """One try at running a piece of work through one runtime.

    Several attempts may belong to a single ProviderRun: that is internal
    resilience, not several pieces of work. Kept on the run, never shown raw.
    """

    runtime_id: str
    kind: str
    started_at: datetime
    completed_at: datetime | None = None
    # "completed" | "failed" | "cancelled" | "skipped"
    outcome: str = "failed"
    failure_kind: str | None = None
    # Why Bevro moved on (or did not), in short technical words. Server-side only.
    fallback_reason: str | None = None
    # Whether this attempt's artifacts became the task's.
    contributed: bool = False


# --------------------------------------------------------------------------- fallback policy

# A failure of the *runtime itself*: the work never really started here, so the
# same request may safely be tried through another way of reaching the provider.
RUNTIME_LEVEL_FAILURES = frozenset({FailureKind.PROVIDER_UNAVAILABLE, FailureKind.CONFIGURATION_PROBLEM})
# The provider took the work and it went wrong, the person stopped it, or the
# answer was unusable: another runtime would do exactly the same thing.
TASK_LEVEL_FAILURES = frozenset({FailureKind.INVOCATION_FAILED, FailureKind.OUTPUT_INVALID, FailureKind.CANCELLED})


def may_fall_back(failure: FailureKind | None, *, allow_on_timeout: bool = False) -> bool:
    """Does this failure say the *runtime* was at fault, rather than the work?

    Credentials are decided per pair of runtimes (a different strategy may
    succeed where this one could not), so they are not answered here.
    """
    if failure is None:
        return False
    if failure in RUNTIME_LEVEL_FAILURES:
        return True
    if failure == FailureKind.TIMED_OUT:
        return allow_on_timeout
    return False


def credentials_differ(failed: "RuntimeProfile", candidate: "RuntimeProfile") -> bool:
    """After a credential failure, only a runtime that gets its secrets a
    different way is worth trying."""
    if candidate.credentials.strategy != failed.credentials.strategy:
        return True
    return set(candidate.credentials.names) != set(failed.credentials.names)


class RuntimeProfile(BaseModel):
    id: str = Field(max_length=40)
    kind: RuntimeKind = RuntimeKind.UNKNOWN
    # Plain wording: "Already running on this machine", "Runs from this project", "Uses MCP"...
    display_name: str = Field(max_length=120)
    confidence: str = "medium"  # high | medium | low
    availability: str = "ready"  # ready | needs_worker | needs_start | not_invocable
    # Invocation metadata: the adapter block Bevro runs. Server-side only.
    adapter: dict[str, Any] = Field(default_factory=dict)
    # Health-check metadata (a path, a unit name...). Server-side only.
    health: dict[str, Any] = Field(default_factory=dict)
    credentials: Credentials = Field(default_factory=Credentials)
    # Working directory, URL, unit name... Server-side only.
    target: str | None = None
    input: InputMode = InputMode.NONE
    outputs: list[OutputMode] = Field(default_factory=list)
    abilities: RuntimeAbilities = Field(default_factory=RuntimeAbilities)
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Rank among a provider's runtimes; higher is preferred. Set by the ranking rules.
    priority: int = 0
    # What recent runs and checks say about this way of reaching the provider.
    health: RuntimeHealth = Field(default_factory=RuntimeHealth)

    @property
    def adapter_kind(self) -> str:
        return str(self.adapter.get("kind") or ADAPTER_FOR_KIND.get(self.kind, ""))

    @property
    def invocable(self) -> bool:
        return self.availability != "not_invocable" and bool(self.adapter_kind) and self.abilities.accepts_prompt

    def public(self) -> dict[str, Any]:
        """What a browser may know about a runtime: wording, never mechanism."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "availability": self.availability,
            "confidence": self.confidence,
            "credentials": {
                "label": credentials_label(self.credentials),
                "required_from_user": self.credentials.required_from_user,
                "names": list(self.credentials.names),
                "note": self.credentials.note,
            },
            "abilities": self.abilities.model_dump(),
            "health": self.health.state.value,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
            "invocable": self.invocable,
        }

    def advanced(self) -> dict[str, Any]:
        """Technical detail for "Advanced details": kinds and mechanisms, never paths or secrets."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "adapter": self.adapter_kind,
            "input": self.input.value,
            "outputs": [o.value for o in self.outputs],
            "credential_strategy": self.credentials.strategy.value,
            "credential_names": list(self.credentials.names),
            "priority": self.priority,
            "health": self.health.state.value,
            "consecutive_failures": self.health.consecutive_failures,
        }


CREDENTIAL_LABELS = {
    CredentialStrategy.RUNTIME_MANAGED: "Managed by provider",
    CredentialStrategy.INHERITED_ENVIRONMENT: "Managed by provider",
    CredentialStrategy.PROJECT_DOTENV: "Managed by provider",
    CredentialStrategy.DOCKER_ENVIRONMENT: "Managed by provider",
    CredentialStrategy.SYSTEMD_ENVIRONMENT: "Managed by provider",
    CredentialStrategy.EXTERNAL_SECRET_STORE: "Managed by provider",
    CredentialStrategy.BEVRO_MANAGED: "Managed by Bevro",
    CredentialStrategy.NONE: "None needed",
    CredentialStrategy.UNKNOWN: "Unknown",
}


def credentials_label(credentials: Credentials) -> str:
    if credentials.required_from_user:
        return "Missing"
    return CREDENTIAL_LABELS.get(credentials.strategy, "Unknown")


@runtime_checkable
class RuntimeAdapter(Protocol):
    """The one contract every runtime mechanism maps into.

    ProviderRun talks to a RuntimeProfile; the profile's adapter kind picks
    one of these. Nothing above this line knows whether the target is HTTP,
    Docker, systemd, a CLI or MCP.
    """

    kind: str
    execution: str
    requires: frozenset[str]

    def health(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult: ...

    def invoke(self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult: ...

    def status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult: ...

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool: ...

    def collect_artifacts(self, provider: ProviderSpec, result: InvocationResult, context: InvocationContext | None = None) -> list[ArtifactDraft]: ...


class BaseRuntimeAdapter:
    """Defaults so an adapter only writes what its mechanism needs.

    `check` and `get_status` are the older names; they keep working so that
    existing adapters and tests do not have to move at once.
    """

    kind: str = ""
    execution: str = "inline"
    requires: frozenset[str] = frozenset()

    def health(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult:
        return self.check(provider, secrets)

    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult:  # pragma: no cover - overridden
        return HealthResult(ok=True)

    def status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        return self.get_status(provider, external_ref, secrets)

    def get_status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        from adapters.base import NotSupported

        raise NotSupported(f"{self.kind} runtimes have no status call")

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool:
        from adapters.base import NotSupported

        raise NotSupported(f"{self.kind} runtimes cannot be cancelled once started")

    def collect_artifacts(self, provider: ProviderSpec, result: InvocationResult, context: InvocationContext | None = None) -> list[ArtifactDraft]:
        """Output normalisation: by default what invoke() already proposed."""
        return list(result.artifacts)
