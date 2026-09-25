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

# Ways of getting a credential that belong to the runtime rather than to Bevro.
SELF_SUPPLYING = (
    CredentialStrategy.RUNTIME_MANAGED,
    CredentialStrategy.DOCKER_ENVIRONMENT,
    CredentialStrategy.SYSTEMD_ENVIRONMENT,
    CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE,
    CredentialStrategy.PROJECT_DOTENV,
    CredentialStrategy.INHERITED_ENVIRONMENT,
)

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
    elif strategy in SELF_SUPPLYING:
        value += 4
    elif strategy == CredentialStrategy.UNKNOWN:
        value -= 4
    # Whether this way in has what it needs. Decisive between two that are
    # otherwise alike - a command line that cannot reach the key is a worse
    # way in than the service that is handed it - and deliberately smaller
    # than the penalties for not being usable at all, because a service with
    # its credentials that is not running is still not running.
    if rt.credentials.status == "configured":
        value += 5
    elif rt.credentials.status == "incomplete":
        value -= 5
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


def settle_credentials(runtimes: list[RuntimeProfile]) -> list[RuntimeProfile]:
    """Decide, across all the ways in, whether anyone has to be asked.

    Three things that look the same from one runtime and are not:

        this way in cannot get it     the command line, with nothing in the
                                      environment and no .env
        nothing here has it           and so somebody has to be asked
        another way in already has it the installed service, whose unit
                                      hands it an environment of its own

    Only the second is a question for a person. Where some other runtime
    supplies a name, the ones that cannot are marked as such - they still
    cannot, and Advanced details still says so - but nobody is asked for
    something this machine already has.
    """
    # Only a way in that can actually take work. An installed service that
    # runs on a timer has its credentials and cannot be handed anything, so
    # its having them is no help to a person asking Bevro to do something -
    # and saying "it already has what it needs" would be a promise Bevro
    # cannot keep.
    supplied: set[str] = {name for rt in runtimes if rt.invocable for name in rt.credentials.supplied}
    if not supplied:
        # Nothing usable supplies anything - but something unusable might,
        # and that is worth saying rather than swallowing.
        _explain_the_near_miss(runtimes)
        return runtimes
    for rt in runtimes:
        creds = rt.credentials
        if not creds.required_from_user or not creds.missing:
            continue
        if not all(name in supplied for name in creds.missing):
            continue
        # Said in the words of whichever way in actually has it - "Uses
        # credentials provided by the installed system service" - because
        # that is the useful half. Where it leaves this one is a technical
        # fact, and lives under Advanced details with everything else.
        holder = next((other for other in runtimes if other.invocable and set(creds.missing) <= set(other.credentials.supplied)), None)
        note = (holder.credentials.note if holder is not None else None) or "Another way into this project already has what it needs."
        rt.credentials = creds.model_copy(update={"required_from_user": False, "supplied_elsewhere": True, "note": note})
    _explain_the_near_miss(runtimes)
    return runtimes


# What holds the credential, in a few words that fit inside a sentence.
WHAT_HOLDS_IT = {
    RuntimeKind.SYSTEMD: "installed system service",
    RuntimeKind.DOCKER_COMPOSE: "container service",
    RuntimeKind.PROCESS: "running service",
}


def _explain_the_near_miss(runtimes: list[RuntimeProfile]) -> None:
    """Say when the credential is here but out of Bevro's reach.

    A project whose installed service is a scheduled job has its credentials
    and cannot be handed anything. "Needs a credential" on its own would look
    like Bevro had not noticed; saying what was noticed, and why it does not
    help, is the difference between a wrong answer and a complete one.
    """
    out_of_reach: set[str] = {name for rt in runtimes if not rt.invocable for name in rt.credentials.supplied}
    if not out_of_reach:
        return
    for rt in runtimes:
        creds = rt.credentials
        if not creds.required_from_user or not set(creds.missing) & out_of_reach:
            continue
        holder = next((other for other in runtimes if not other.invocable and set(creds.missing) <= set(other.credentials.supplied)), None)
        where = WHAT_HOLDS_IT.get(holder.kind, "another part of it") if holder is not None else "another part of it"
        rt.credentials = creds.model_copy(
            update={
                # Somewhere in this project has it - just not anywhere Bevro
                # can use. Marked, so that the interface knows there is
                # something worth saying beyond "missing".
                "supplied_elsewhere": True,
                "note": (
                    f"Its {where} has its own, but Bevro can't hand work to that. "
                    f"To run this directly, Bevro needs its own {', '.join(creds.missing)}."
                ),
            }
        )


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
