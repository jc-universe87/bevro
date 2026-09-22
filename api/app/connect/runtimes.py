"""Building and ranking RuntimeProfiles during discovery.

The builders turn evidence (an entry point, a port that answers, a compose
service, a unit file) into provider-neutral profiles. `rank()` orders them by
the rules below and `select()` picks one or says a choice is needed.

Preference, where sensible:
  1. an already-running service or API
  2. an established managed runtime (Compose, systemd) that can be reached
  3. a declared MCP / CLI interface
  4. an inferred executable entry point
  5. a Bevro-side wrapper, last of all
"""

from __future__ import annotations

from typing import Any

from adapters.runtime import CredentialStrategy, Credentials, HealthState, InputMode, OutputMode, RuntimeAbilities, RuntimeKind, RuntimeProfile

CONF = {"high": 0, "medium": 1, "low": 2}

# Base score by how native and how safe the mechanism is.
BASE_SCORE = {
    RuntimeKind.PROCESS: 100,
    RuntimeKind.DOCKER_COMPOSE: 85,
    RuntimeKind.SYSTEMD: 85,
    RuntimeKind.HTTP: 80,
    RuntimeKind.MCP_HTTP: 80,
    RuntimeKind.MCP_STDIO: 70,
    RuntimeKind.CLI: 60,
    RuntimeKind.PYTHON_ENTRYPOINT: 60,
    RuntimeKind.NODE_ENTRYPOINT: 60,
    RuntimeKind.FILE_EXCHANGE: 30,
    RuntimeKind.BUILTIN: 90,
    RuntimeKind.UNKNOWN: 0,
}
CHOICE_MARGIN = 12  # closer than this between different kinds: ask the person

# Enough that a native way in of similar standing always comes first, small
# enough that a healthy built connection still beats one that is down.
BUILT_CONNECTION_PENALTY = -18

# Recent experience, applied on top of the mechanism's own standing.
HEALTH_SCORE = {
    HealthState.AVAILABLE: 4,
    HealthState.UNKNOWN: 0,
    HealthState.DEGRADED: -15,
    HealthState.UNAVAILABLE: -30,
}


def score(rt: RuntimeProfile) -> int:
    value = BASE_SCORE.get(rt.kind, 0)
    # Something that can take a task now beats something that must be started first.
    value += {"ready": 10, "needs_worker": 0, "needs_start": -40, "not_invocable": -100}.get(rt.availability, -50)
    value += {"high": 6, "medium": 0, "low": -10}.get(rt.confidence, 0)
    if not rt.abilities.accepts_prompt:
        value -= 60
    if rt.abilities.status:
        value += 2
    if rt.abilities.cancel:
        value += 2
    if rt.abilities.file_artifacts:
        value += 1
    strategy = rt.credentials.strategy
    if rt.credentials.required_from_user:
        value -= 6
    elif strategy in (CredentialStrategy.RUNTIME_MANAGED, CredentialStrategy.DOCKER_ENVIRONMENT, CredentialStrategy.SYSTEMD_ENVIRONMENT, CredentialStrategy.PROJECT_DOTENV, CredentialStrategy.INHERITED_ENVIRONMENT):
        value += 4
    elif strategy == CredentialStrategy.UNKNOWN:
        value -= 4
    # A connection Bevro had built for a project that had none is the last
    # resort: anything the project offers itself, of comparable standing, wins.
    if (rt.adapter.get("config") or {}).get("bridge"):
        value += BUILT_CONNECTION_PENALTY
    # What recent runs said. A slightly less preferred runtime that works now
    # beats a preferred one that is known to be down.
    value += HEALTH_SCORE.get(rt.health.state, 0)
    if rt.health.in_cooldown():
        value -= 25
    return value


def rank(runtimes: list[RuntimeProfile]) -> list[RuntimeProfile]:
    ranked = sorted(runtimes, key=lambda rt: (-score(rt), CONF.get(rt.confidence, 2), rt.id))
    for rt in ranked:
        rt.priority = score(rt)
    return ranked


def select(runtimes: list[RuntimeProfile]) -> tuple[str | None, bool]:
    """(active runtime id, choice needed). Invocable profiles only; the person is
    asked when two different mechanisms are close."""
    ranked = [rt for rt in rank(runtimes) if rt.invocable]
    if not ranked:
        return (runtimes[0].id if runtimes else None), False
    best = ranked[0]
    choice = any(rt.kind != best.kind and best.priority - rt.priority < CHOICE_MARGIN and rt.availability in ("ready", "needs_worker") for rt in ranked[1:])
    return best.id, choice


# --------------------------------------------------------------------------- builders

def unique_id(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def http_runtime(rid: str, *, kind: RuntimeKind, adapter: dict[str, Any], display_name: str, availability: str, confidence: str, credentials: Credentials, evidence: list[str], warnings: list[str] | None = None, target: str | None = None, accepts_prompt: bool = True) -> RuntimeProfile:
    config = adapter.get("config") or {}
    return RuntimeProfile(
        id=rid,
        kind=kind,
        display_name=display_name,
        confidence=confidence,
        availability=availability,
        adapter=adapter,
        health={"path": config.get("health_path")} if config.get("health_path") else {},
        credentials=credentials,
        target=target or (str(config.get("base_url")) if config.get("base_url") else None),
        input=InputMode.HTTP_JSON,
        outputs=[OutputMode.JSON_RESPONSE, OutputMode.DEEP_LINK] if config.get("invoke") else [OutputMode.HTTP_RESULT, OutputMode.DEEP_LINK],
        abilities=RuntimeAbilities(accepts_prompt=accepts_prompt, status=bool(config.get("status_path")), cancel=bool(config.get("cancel_path")), health=True),
        evidence=evidence,
        warnings=warnings or [],
    )


def mcp_runtime(rid: str, *, stdio: bool, adapter: dict[str, Any], display_name: str, availability: str, confidence: str, credentials: Credentials, evidence: list[str], warnings: list[str] | None = None, target: str | None = None) -> RuntimeProfile:
    tool = (adapter.get("config") or {}).get("tool") or {}
    return RuntimeProfile(
        id=rid,
        kind=RuntimeKind.MCP_STDIO if stdio else RuntimeKind.MCP_HTTP,
        display_name=display_name,
        confidence=confidence,
        availability=availability,
        adapter=adapter,
        credentials=credentials,
        target=target,
        input=InputMode.MCP_ARGUMENT,
        outputs=[OutputMode.MCP_RESULT],
        abilities=RuntimeAbilities(accepts_prompt=bool(tool.get("name")), background=stdio, health=True),
        evidence=evidence,
        warnings=warnings or [],
    )


def cli_runtime(rid: str, *, kind: RuntimeKind, adapter: dict[str, Any], display_name: str, availability: str, confidence: str, credentials: Credentials, evidence: list[str], warnings: list[str] | None = None, target: str | None = None) -> RuntimeProfile:
    config = adapter.get("config") or {}
    mode = str((config.get("input") or {}).get("mode") or "argument")
    outputs = [OutputMode.STDOUT, OutputMode.FILES]
    if (config.get("output") or {}).get("report_dir"):
        outputs.append(OutputMode.REPORT_DIR)
    return RuntimeProfile(
        id=rid,
        kind=kind,
        display_name=display_name,
        confidence=confidence,
        availability=availability,
        adapter=adapter,
        credentials=credentials,
        target=target,
        input=InputMode(mode) if mode in {m.value for m in InputMode} else InputMode.ARGUMENT,
        outputs=outputs,
        abilities=RuntimeAbilities(accepts_prompt=mode != "none", background=True, cancel=True, streaming=True, file_artifacts=True, health=True),
        evidence=evidence,
        warnings=warnings or [],
    )


def managed_only_runtime(rid: str, *, kind: RuntimeKind, display_name: str, evidence: list[str], note: str, credentials: Credentials, target: str | None = None) -> RuntimeProfile:
    """A runtime Bevro can see but cannot hand tasks to (a timer job, a unit
    without an interface). Recorded for the record and for Manage; never active."""
    return RuntimeProfile(
        id=rid,
        kind=kind,
        display_name=display_name,
        confidence="high",
        availability="not_invocable",
        adapter={},
        credentials=credentials,
        target=target,
        input=InputMode.NONE,
        outputs=[],
        abilities=RuntimeAbilities(accepts_prompt=False, health=False),
        evidence=evidence,
        warnings=[note],
    )
