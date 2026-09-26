"""The runtime layer: ProviderRun talks to a RuntimeProfile, never to a transport.

    provider.runtimes           every way this installation can run or be reached
    provider.active_runtime     the one Bevro uses
    adapter_for(runtime)        Bevro's implementation of that mechanism
    eligible_runtimes(...)      ranked, health-aware, credential-aware candidates
    execute(provider, ...)      attempts them in order: invoke() + collect_artifacts()
    health(provider, ...)       health() through the contract

Several runtimes may reach the same provider. Execution ranks them afresh each
time, uses the best usable one, and falls back to the next only when the
failure says the *runtime* was at fault. That is internal resilience: one
ProviderRun, one piece of work, whatever it took to get there.

Nothing in the task service, the worker or the routers branches on a runtime
kind or a provider; they call this module. Adding a runtime mechanism means a
new adapter (adapters/) and an entry in ADAPTER_FOR_KIND, nothing else.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters import ArtifactDraft, FailureKind, InvocationContext, InvocationRequest, InvocationResult, ProviderSpec, ResultState, get_adapter
from adapters.base import HealthResult, NotSupported
from adapters.registry import execution_mode
from adapters.runtime import (
    ADAPTER_FOR_KIND,
    API,
    WORKER,
    CredentialStrategy,
    Credentials,
    HealthState,
    InputMode,
    OutputMode,
    RuntimeAbilities,
    RuntimeAttempt,
    RuntimeHealth,
    RuntimeKind,
    RuntimeProfile,
    credentials_differ,
    may_fall_back,
)
from app.models import Provider

log = logging.getLogger("bevro.runtime")

# Human wording per kind when a profile does not carry its own.
DISPLAY_NAMES = {
    RuntimeKind.HTTP: "Connected over the network",
    RuntimeKind.MCP_HTTP: "Uses MCP over the network",
    RuntimeKind.MCP_STDIO: "Uses MCP on this machine",
    RuntimeKind.CLI: "Runs on this machine",
    RuntimeKind.DOCKER_COMPOSE: "Runs as a local service",
    RuntimeKind.SYSTEMD: "Runs as a local service",
    RuntimeKind.PROCESS: "Already running on this machine",
    RuntimeKind.PYTHON_ENTRYPOINT: "Runs from this project",
    RuntimeKind.NODE_ENTRYPOINT: "Runs from this project",
    RuntimeKind.FILE_EXCHANGE: "Exchanges files",
    RuntimeKind.BUILTIN: "Built into Bevro",
    RuntimeKind.UNKNOWN: "Not connected yet",
}


# --------------------------------------------------------------------------- profiles on a provider

def runtimes_of(provider: Provider) -> list[RuntimeProfile]:
    """Every way into this provider, with the cross-runtime picture applied.

    What each way in needs is evidence and is stored. Whether anyone has to
    be *asked* for it depends on all of them together, so that conclusion is
    worked out here, on the way out, rather than written down at discovery
    and left to go stale. It costs nothing - no files, no network - and it
    means improving the wording improves what is already connected.
    """
    from app.connect.runtimes import settle_credentials

    out: list[RuntimeProfile] = []
    for raw in provider.runtimes or []:
        try:
            out.append(RuntimeProfile.model_validate(raw))
        except Exception:  # noqa: BLE001 - one bad row must not hide the others
            log.warning("provider %s has an unreadable runtime profile", provider.slug)
    return settle_credentials(out)


def active_runtime(provider: Provider) -> RuntimeProfile | None:
    profiles = runtimes_of(provider)
    if not profiles:
        return None
    for rt in profiles:
        if rt.id == provider.active_runtime:
            return rt
    return profiles[0]


def set_runtimes(provider: Provider, runtimes: list[RuntimeProfile], active_id: str | None = None) -> None:
    """Store the profiles and keep the denormalised adapter block in step."""
    provider.runtimes = [rt.model_dump(mode="json") for rt in runtimes]
    chosen = next((rt for rt in runtimes if rt.id == active_id), runtimes[0] if runtimes else None)
    provider.active_runtime = chosen.id if chosen else None
    if chosen is not None:
        block = dict(chosen.adapter)
        block.setdefault("kind", chosen.adapter_kind)
        method = {"http": "api", "mcp": "mcp", "command": "command"}.get(chosen.adapter_kind)
        if method and provider.origin != "example":
            block.setdefault("method", method)
        provider.adapter = block


def _now() -> datetime:
    return datetime.now(timezone.utc)


def adapter_for(runtime: RuntimeProfile):
    kind = runtime.adapter_kind
    if not kind:
        raise NotSupported(f"runtime {runtime.kind} has no adapter")
    return get_adapter(kind)


def adapter_block(provider: Provider, runtime: RuntimeProfile | None = None) -> dict[str, Any]:
    """The block an adapter runs with.

    `provider.adapter` mirrors the *active* runtime, so code that edits it
    directly keeps working. Any other runtime - one Bevro fell back to -
    brings its own block; taking the active one would silently run the wrong
    thing.
    """
    rt = runtime or active_runtime(provider)
    if rt is None:
        return dict(provider.adapter)
    profiles = runtimes_of(provider)
    active_id = provider.active_runtime or (profiles[0].id if profiles else None)
    mirrors_active = rt.id == active_id and bool(provider.adapter.get("kind"))
    block = dict(provider.adapter) if mirrors_active else dict(rt.adapter)
    if not block.get("kind"):
        block["kind"] = rt.adapter_kind
    return block


def _adapter(provider: Provider, runtime: RuntimeProfile | None = None):
    return get_adapter(str(adapter_block(provider, runtime).get("kind", "")))


def spec_for(provider: Provider, runtime: RuntimeProfile | None = None) -> ProviderSpec:
    return ProviderSpec(id=str(provider.id), slug=provider.slug, name=provider.name, capabilities=provider.capabilities, adapter=adapter_block(provider, runtime), app_url=provider.app_url)


def execution_of_runtime(provider: Provider, runtime: RuntimeProfile) -> str:
    try:
        adapter = _adapter(provider, runtime)
    except NotSupported:
        return "inline"
    return execution_mode(adapter, adapter_block(provider, runtime))


# Runtimes reached over a network: which process can see them is a question
# worth asking, because the answer differs. Everything else is decided by the
# mechanism alone - a command on the host is the worker's by nature.
NETWORK_KINDS = frozenset({RuntimeKind.HTTP, RuntimeKind.OPENAPI, RuntimeKind.MCP_HTTP, RuntimeKind.DOCKER_COMPOSE, RuntimeKind.SYSTEMD, RuntimeKind.PROCESS})


def is_network(runtime: RuntimeProfile) -> bool:
    return runtime.kind in NETWORK_KINDS and not (runtime.adapter.get("config") or {}).get("argv")


def locations_for(provider: Provider, runtime: RuntimeProfile, *, worker_available: bool | None = None) -> list[str]:
    """Where this runtime could be driven from, least dependent first.

    A mechanism that needs the host is the worker's whatever anyone can
    reach. A network runtime may be visible to the API, to the worker, to
    both or to neither - and what was actually tried is remembered on the
    runtime, so a location already ruled out is not offered again.
    """
    if execution_of_runtime(provider, runtime) == "background" and not is_network(runtime):
        return [WORKER] if worker_available is not False else []
    if not is_network(runtime):
        return [API]

    reach = runtime.reachability
    options: list[str] = []
    # The API needs no other process, so it is always the first choice when
    # it has not been ruled out.
    if not reach.ruled_out(API):
        options.append(API)
    if not reach.ruled_out(WORKER) and worker_available is not False:
        options.append(WORKER)
    # Somewhere known to work comes before somewhere merely untried.
    options.sort(key=lambda loc: 0 if reach.state(loc) == "available" else 1)
    return options


def execution_location(provider: Provider, runtime: RuntimeProfile, *, worker_available: bool | None = None) -> str | None:
    """The one place this runtime should be driven from, or None if nowhere."""
    options = locations_for(provider, runtime, worker_available=worker_available)
    return options[0] if options else None


def execution_of(provider: Provider, worker_available: bool | None = None) -> str:
    """Where a run for this provider belongs: the API thread, or the worker.

    The least dependent location that can actually reach it. A provider whose
    network runtime the API can see is run here; one the API cannot see goes
    to the worker, which may be on a network the container is not.
    """
    profiles = [rt for rt in runtimes_of(provider) if rt.invocable]
    if not profiles:
        try:
            return execution_mode(_adapter(provider), adapter_block(provider))
        except NotSupported:
            return "inline"
    modes = {execution_of_runtime(provider, rt) for rt in profiles}
    if "background" in modes:
        # Something here needs the host. The worker can drive every mechanism,
        # so it takes the whole provider and every way in stays available to
        # fall back to - unless there is no worker, when a way the API can
        # reach is better than waiting.
        if "inline" not in modes:
            return "background"
        return "inline" if worker_available is False else "background"

    # Everything here is reached over a network, so the only question is who
    # can see it. The API needs no other process, so it goes first.
    network = [rt for rt in profiles if is_network(rt)]
    if any(API in locations_for(provider, rt, worker_available=worker_available) for rt in network):
        return "inline"
    if any(WORKER in locations_for(provider, rt, worker_available=worker_available) for rt in network):
        return "background"
    return "inline" if not network else "background"


def reachable_without_worker(provider: Provider) -> bool:
    """Is there a usable way in that this process can drive itself?

    A network runtime the API has already failed to reach does not count: it
    is a way in for the worker, not for this process.
    """
    for rt in runtimes_of(provider):
        if not rt.invocable or execution_of_runtime(provider, rt) != "inline":
            continue
        if is_network(rt) and rt.reachability.ruled_out(API):
            continue
        return True
    return False


def requires_of(provider: Provider) -> frozenset[str]:
    try:
        adapter = _adapter(provider)
    except NotSupported:
        return frozenset()
    return frozenset(getattr(adapter, "requires", frozenset()))


# --------------------------------------------------------------------------- the contract

# Reserved keys on a result's metadata, filled in here and taken off the result
# by the task service: which runtime ran the work, every attempt it took, and
# the health each runtime earned.
META_RUNTIME = "runtime"
META_ATTEMPTS = "attempts"
META_HEALTH = "runtime_health"

# A runtime is never tried twice for one piece of work, and a provider with a
# long list of ways in never turns into a long wait.
MAX_ATTEMPTS = 4


def credentials_satisfied(runtime: RuntimeProfile, secrets: dict[str, str]) -> bool:
    """Can this runtime get the secrets it needs?

    Bevro only has to supply them under `bevro_managed`; every other strategy
    is the provider's or the machine's business, and the adapter's own
    pre-flight has the last word at run time.
    """
    if runtime.credentials.strategy != CredentialStrategy.BEVRO_MANAGED:
        return True
    return all(name in secrets for name in runtime.credentials.names)


def eligible_runtimes(provider: Provider, secrets: dict[str, str] | None = None, *, now: datetime | None = None, execution: str | None = None, explain: bool = True) -> tuple[list[RuntimeProfile], list[tuple[RuntimeProfile, str]]]:
    """(usable runtimes, best first), (skipped runtime, why).

    Ranking is the same logic discovery uses, so nothing needs to agree twice;
    it already accounts for health and cooldown. `execution` is where the run
    is happening ("inline" in the API, "background" in the worker): a runtime
    that needs the host is no use to a run inside the API container.
    """
    from app.connect.runtimes import rank

    moment = now or _now()
    usable: list[RuntimeProfile] = []
    skipped: list[tuple[RuntimeProfile, str]] = []
    for rt in rank(runtimes_of(provider)):
        if not rt.invocable:
            skipped.append((rt, "cannot take a task"))
            continue
        try:
            adapter_for(rt)
        except NotSupported:
            skipped.append((rt, "no adapter for this mechanism"))
            continue
        if execution == "inline" and execution_of_runtime(provider, rt) == "background":
            skipped.append((rt, "needs the worker on the host"))
            continue
        if not credentials_satisfied(rt, secrets or {}):
            skipped.append((rt, "credential"))
            continue
        if rt.health.in_cooldown(moment):
            skipped.append((rt, "cooling down after a failure"))
            continue
        usable.append(rt)
    if not usable and explain:
        # Nothing is usable as it stands. Rather than refuse the work with a
        # vague sentence, put forward the best runtime there is: a rest period
        # is only a preference, and a runtime that cannot take a task explains
        # itself far better than a general "not available" ever could.
        #
        # This is for producing a good failure, not for deciding what a
        # provider *is*: `explain=False` asks the plain question, and is what
        # reconciliation uses.
        for reason in ("cooling down after a failure", "cannot take a task", "no adapter for this mechanism"):
            candidate = next((rt for rt, why in skipped if why == reason), None)
            if candidate is not None:
                usable = [candidate]
                skipped = [(rt, why) for rt, why in skipped if rt is not candidate]
                break
    return usable, skipped


def why_none_usable(skipped: list[tuple[RuntimeProfile, str]]) -> str:
    """Why no way in can be used, in the order a person would care about.

    The one rule for it: the status an item shows (reconcile.state_of) and a
    run that could not start both ask here, so they cannot disagree.

    A missing credential is the answer only when it is what stands in the
    way of *every* way in that could take work. A way in that needs nothing
    from the person but is down right now is the truer answer: getting it
    back gives Bevro the app without anyone handing over a key.
    """
    workable = [(rt, why) for rt, why in skipped if why not in ("cannot take a task", "no adapter for this mechanism")]
    others = [(rt, why) for rt, why in workable if why != "credential"]
    if workable and not others:
        return "needs_credential"
    reasons = {why for _rt, why in others}
    if "needs the worker on the host" in reasons:
        return "waiting_for_worker"
    if "cooling down after a failure" in reasons:
        resting = [rt for rt, why in others if why == "cooling down after a failure"]
        return "needs_start" if all(rt.availability == "needs_start" for rt in resting) else "unreachable"
    return "nothing_usable"


def _credential_failure(provider: Provider, skipped: list[tuple[RuntimeProfile, str]]) -> InvocationResult | None:
    """Nothing could run because every way in lacks a credential (why_none_usable)."""
    if why_none_usable(skipped) != "needs_credential":
        return None
    blocked = [rt for rt, why in skipped if why == "credential"]
    return InvocationResult(
        state=ResultState.FAILED,
        error=f"{provider.name} needs a credential before it can run.",
        failure=FailureKind.CREDENTIAL_REQUIRED,
        metadata={"missing_secrets": list(blocked[0].credentials.names)},
    )


def _invoke_once(provider: Provider, runtime: RuntimeProfile, request: InvocationRequest, context: InvocationContext | None) -> tuple[InvocationResult, list[ArtifactDraft]]:
    """One runtime, one try. Never raises: a provider must not take Bevro down."""
    try:
        adapter = _adapter(provider, runtime)
    except NotSupported as exc:
        return InvocationResult(state=ResultState.FAILED, error=str(exc), failure=FailureKind.CONFIGURATION_PROBLEM), []
    spec = spec_for(provider, runtime)
    try:
        result = adapter.invoke(spec, request, context)
    except NotSupported as exc:
        return InvocationResult(state=ResultState.FAILED, error=str(exc), failure=FailureKind.CONFIGURATION_PROBLEM), []
    except Exception:  # noqa: BLE001
        log.exception("provider %s raised during invoke", provider.slug)
        return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} could not complete this request.", failure=FailureKind.INVOCATION_FAILED), []
    try:
        collect = getattr(adapter, "collect_artifacts", None)
        artifacts = collect(spec, result, context) if callable(collect) else list(result.artifacts)
    except Exception:  # noqa: BLE001
        log.exception("provider %s raised while collecting artifacts", provider.slug)
        artifacts = list(result.artifacts)
    return result, artifacts


def execute(provider: Provider, request: InvocationRequest, context: InvocationContext | None = None, *, execution: str | None = None) -> tuple[InvocationResult, list[ArtifactDraft]]:
    """Run one piece of work through the best runtime available, falling back
    when a runtime - not the work - was at fault. Never raises.

    The result's metadata carries which runtime ran it, every attempt, and the
    health each runtime earned; the task service takes those off and stores them.
    """
    from app.config import get_settings

    allow_timeout_fallback = bool(getattr(get_settings(), "runtime_fallback_on_timeout", False))
    usable, skipped = eligible_runtimes(provider, request.secrets, execution=execution)
    attempts: list[RuntimeAttempt] = []
    health: dict[str, RuntimeHealth] = {}

    if not usable:
        blocked = _credential_failure(provider, skipped)
        if blocked is not None:
            log.info("run %s: no runtime could run %s (credential)", request.run_id, provider.slug)
            return _with_meta(blocked, None, attempts, health), []
        reason = skipped[0][1] if skipped else "no runtime configured"
        log.info("run %s: no runtime available for %s (%s)", request.run_id, provider.slug, reason)
        return _with_meta(InvocationResult(state=ResultState.FAILED, error=f"{provider.name} isn't available right now.", failure=FailureKind.PROVIDER_UNAVAILABLE), None, attempts, health), []

    last_result: InvocationResult | None = None
    previous: RuntimeProfile | None = None
    # Ways in that failed for their own reasons, best first.
    failed: list[tuple[InvocationResult, list[ArtifactDraft], RuntimeProfile, RuntimeAttempt]] = []
    for runtime in usable[:MAX_ATTEMPTS]:
        if previous is not None:
            # Only worth trying if this runtime gets its secrets another way.
            if last_result is not None and last_result.failure == FailureKind.CREDENTIAL_REQUIRED and not credentials_differ(previous, runtime):
                attempts.append(RuntimeAttempt(runtime_id=runtime.id, kind=runtime.kind.value, started_at=_now(), completed_at=_now(), outcome="skipped", fallback_reason="same credential strategy as the runtime that just failed"))
                continue
            log.info("run %s: falling back to runtime %s (%s) for %s", request.run_id, runtime.id, runtime.kind.value, provider.slug)
            if context is not None:
                try:
                    context.falling_back()
                except Exception:  # noqa: BLE001 - saying so must never stop the next attempt
                    log.exception("run %s: could not record the fallback", request.run_id)
        started = _now()
        log.info("run %s: attempting runtime %s (%s) for %s", request.run_id, runtime.id, runtime.kind.value, provider.slug)
        result, artifacts = _invoke_once(provider, runtime, request, context)
        attempt = RuntimeAttempt(runtime_id=runtime.id, kind=runtime.kind.value, started_at=started, completed_at=_now(), outcome=str(result.state.value))

        if result.state != ResultState.FAILED:
            attempt.contributed = True
            attempts.append(attempt)
            # Cancellation is the person's decision: record it, never try elsewhere.
            if result.state == ResultState.CANCELLED:
                log.info("run %s: cancelled during runtime %s", request.run_id, runtime.id)
            else:
                health[runtime.id] = runtime.health.after_success()
                log.info("run %s: runtime %s completed the work", request.run_id, runtime.id)
            return _with_meta(result, runtime, attempts, health), artifacts

        attempt.failure_kind = (result.failure or FailureKind.INVOCATION_FAILED).value
        runtime_at_fault = may_fall_back(result.failure, allow_on_timeout=allow_timeout_fallback)
        credential_problem = result.failure == FailureKind.CREDENTIAL_REQUIRED
        if runtime_at_fault:
            health[runtime.id] = runtime.health.after_failure(result.metadata.get("detail") or result.error)
        if not (runtime_at_fault or credential_problem):
            attempt.contributed = True
            attempt.fallback_reason = "the work itself failed; another way in would do the same"
            attempts.append(attempt)
            log.info("run %s: runtime %s reported a task-level failure (%s); not falling back", request.run_id, runtime.id, attempt.failure_kind)
            return _with_meta(result, runtime, attempts, health), artifacts

        attempt.fallback_reason = "runtime unavailable" if runtime_at_fault else "credential"
        attempts.append(attempt)
        failed.append((result, artifacts, runtime, attempt))
        last_result, previous = result, runtime

    # Every way in that could be tried was tried, and each failed for a
    # reason of its own rather than the work's. What the person is told is
    # what stopped the best of them, in rank order - the same order the
    # status picks from. A credential is that reason only when every way
    # tried failed for want of one: a way in that needs nothing from the
    # person, but could not be reached, is never overruled by another way
    # that would have needed a key (whether tried or held back).
    assert last_result is not None
    result, artifacts, runtime, attempt = next((f for f in failed if f[0].failure != FailureKind.CREDENTIAL_REQUIRED), failed[0])
    attempt.contributed = True
    log.info("run %s: every runtime for %s failed (%s attempts); telling %s", request.run_id, provider.slug, len(attempts), result.failure)
    return _with_meta(result, runtime, attempts, health), artifacts


def _with_meta(result: InvocationResult, runtime: RuntimeProfile | None, attempts: list[RuntimeAttempt], health: dict[str, RuntimeHealth]) -> InvocationResult:
    meta = dict(result.metadata or {})
    if runtime is not None:
        meta[META_RUNTIME] = {"id": runtime.id, "kind": runtime.kind.value, "display_name": runtime.display_name}
    if attempts:
        meta[META_ATTEMPTS] = [a.model_dump(mode="json") for a in attempts]
    if health:
        meta[META_HEALTH] = {rid: h.model_dump(mode="json") for rid, h in health.items()}
    return result.model_copy(update={"metadata": meta})


def apply_health(provider: Provider, updates: dict[str, Any]) -> bool:
    """Store new health for named runtimes. Returns whether anything changed."""
    if not updates:
        return False
    profiles = runtimes_of(provider)
    changed = False
    for rt in profiles:
        raw = updates.get(rt.id)
        if raw is None:
            continue
        health = RuntimeHealth.model_validate(raw) if not isinstance(raw, RuntimeHealth) else raw
        if rt.health.state != health.state or rt.health.consecutive_failures != health.consecutive_failures:
            log.info("runtime %s of %s is now %s", rt.id, provider.slug, health.state.value)
        rt.health = health
        changed = True
    if changed:
        # JSONB: assign a new list so the change is noticed.
        provider.runtimes = [rt.model_dump(mode="json") for rt in profiles]
    return changed


def health(provider: Provider, secrets: dict[str, str]) -> HealthResult:
    """Check the runtime Bevro would use now, and record what it found.

    A successful check clears a rest period: that is how a runtime that was
    down comes back without anyone reconnecting the provider.
    """
    usable, _skipped = eligible_runtimes(provider, secrets)
    runtime = usable[0] if usable else active_runtime(provider)  # noqa: E501
    result = check_runtime(provider, runtime, secrets) if runtime else HealthResult(ok=False, detail="no runtime configured")
    if runtime is not None:
        apply_health(provider, {runtime.id: (runtime.health.after_success() if result.ok else runtime.health.after_failure(result.detail))})
    return result


def check_runtime(provider: Provider, runtime: RuntimeProfile, secrets: dict[str, str]) -> HealthResult:
    """One runtime's own health check. Never raises."""
    try:
        adapter = _adapter(provider, runtime)
    except NotSupported as exc:
        return HealthResult(ok=False, state="unavailable", detail=str(exc))
    check = getattr(adapter, "health", None) or adapter.check
    try:
        return check(spec_for(provider, runtime), secrets)
    except Exception as exc:  # noqa: BLE001
        log.exception("health check of runtime %s raised", runtime.id)
        return HealthResult(ok=False, state="unavailable", detail=f"{type(exc).__name__}")


def check_all_runtimes(provider: Provider, secrets: dict[str, str], *, execution: str | None = None, location: str = API) -> tuple[list[tuple[RuntimeProfile, HealthResult]], list[RuntimeProfile]]:
    """Test every way in this process can reach, and record what each says.

    Returns (what was checked, what this process cannot reach from here - a
    folder or a command belongs to the worker on the host). A runtime that
    answers is restored, which is how one that was down comes back.

    `location` says which process is asking. For a runtime reached over a
    network that is the whole question: the same address can answer here and
    not there, so the answer is remembered per location rather than as one
    verdict about the runtime.
    """
    outcomes: list[tuple[RuntimeProfile, HealthResult]] = []
    deferred: list[RuntimeProfile] = []
    updates: dict[str, Any] = {}
    reach: dict[str, Any] = {}
    for rt in runtimes_of(provider):
        if not rt.invocable:
            continue
        if execution == "inline" and execution_of_runtime(provider, rt) == "background":
            deferred.append(rt)
            continue
        if execution == "inline" and location == API and is_network(rt) and rt.reachability.host_only():
            # Already known not to answer here. Testing it again would report
            # "not reachable" about a connection the worker uses every day.
            deferred.append(rt)
            continue
        # A credential Bevro has not been given is not a reason to call the
        # provider unreachable: the run itself says "Credential required",
        # which is the thing a person can act on. The check answers the other
        # question - can this way be reached at all.
        result = check_runtime(provider, rt, secrets)
        if is_network(rt):
            reach[rt.id] = rt.reachability.with_result(location, result.ok)
            other = WORKER if location == API else API
            if not result.ok and rt.reachability.state(other) == "available":
                # The other process reaches it. Not seeing it from here says
                # where it is driven from, not that it is down, so it earns
                # no rest period and is left for that process to report on.
                deferred.append(rt)
                continue
        outcomes.append((rt, result))
        updates[rt.id] = rt.health.after_success() if result.ok else rt.health.after_failure(result.detail)
    apply_health(provider, updates)
    apply_reachability(provider, reach)
    return outcomes, deferred


def apply_reachability(provider: Provider, updates: dict[str, Any]) -> None:
    """Record which process could reach which network runtime. Caller commits."""
    if not updates:
        return
    profiles = []
    for rt in runtimes_of(provider):
        if rt.id in updates:
            rt = rt.model_copy(update={"reachability": updates[rt.id]})
        profiles.append(rt)
    provider.runtimes = [rt.model_dump(mode="json") for rt in profiles]


def cancel(provider: Provider, external_ref: str, secrets: dict[str, str]) -> bool:
    return _adapter(provider).cancel(spec_for(provider), external_ref, secrets)


# --------------------------------------------------------------------------- migration of older records

def runtime_from_adapter(adapter: dict[str, Any], *, origin: str = "connected") -> RuntimeProfile:
    """A profile for a provider that only has an adapter block (created before
    runtimes existed). Data in, data out; nothing here names a provider."""
    kind = str(adapter.get("kind") or "")
    config = adapter.get("config") or {}
    if kind == "local":
        return RuntimeProfile(id="builtin", kind=RuntimeKind.BUILTIN, display_name=DISPLAY_NAMES[RuntimeKind.BUILTIN], confidence="high", adapter=adapter, input=InputMode.ARGUMENT, outputs=[OutputMode.HTTP_RESULT], abilities=RuntimeAbilities(accepts_prompt=True))
    if kind == "claude_code":
        return RuntimeProfile(id="cli", kind=RuntimeKind.CLI, display_name="Runs on this machine", confidence="high", availability="needs_worker", adapter=adapter, input=InputMode.ARGUMENT, outputs=[OutputMode.STDOUT, OutputMode.FILES], abilities=RuntimeAbilities(background=True, cancel=True, status=True, streaming=True, file_artifacts=True), credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, note="Uses the machine's own sign-in."))
    if kind == "http":
        names = [str(config["auth"]["secret"])] if isinstance(config.get("auth"), dict) and config["auth"].get("secret") else []
        return RuntimeProfile(id="http", kind=RuntimeKind.HTTP, display_name=DISPLAY_NAMES[RuntimeKind.HTTP], confidence="medium", adapter=adapter, target=str(config.get("base_url") or "") or None, input=InputMode.HTTP_JSON, outputs=[OutputMode.JSON_RESPONSE, OutputMode.DEEP_LINK], abilities=RuntimeAbilities(status=bool(config.get("status_path")), cancel=bool(config.get("cancel_path"))), credentials=Credentials(strategy=CredentialStrategy.BEVRO_MANAGED if names else CredentialStrategy.RUNTIME_MANAGED, names=names))
    if kind == "mcp":
        stdio = bool(config.get("argv")) and not config.get("server_url")
        names = [str(n) for n in config.get("secret_env") or []] or ([str(config["auth"]["secret"])] if isinstance(config.get("auth"), dict) and config["auth"].get("secret") else [])
        return RuntimeProfile(id="mcp", kind=RuntimeKind.MCP_STDIO if stdio else RuntimeKind.MCP_HTTP, display_name=DISPLAY_NAMES[RuntimeKind.MCP_STDIO if stdio else RuntimeKind.MCP_HTTP], confidence="medium", availability="needs_worker" if stdio else "ready", adapter=adapter, target=str(config.get("cwd") or config.get("server_url") or "") or None, input=InputMode.MCP_ARGUMENT, outputs=[OutputMode.MCP_RESULT], abilities=RuntimeAbilities(accepts_prompt=bool((config.get("tool") or {}).get("name")), background=stdio), credentials=Credentials(strategy=CredentialStrategy.BEVRO_MANAGED if names else CredentialStrategy.NONE, names=names))
    if kind == "command":
        names = [str(n) for n in config.get("secret_env") or []]
        self_conf = {str(n) for n in config.get("self_configured") or []}
        strategy = CredentialStrategy.PROJECT_DOTENV if names and all(n in self_conf for n in names) else CredentialStrategy.BEVRO_MANAGED if names else CredentialStrategy.NONE
        mode = str((config.get("input") or {}).get("mode") or "argument")
        return RuntimeProfile(id="cli", kind=RuntimeKind.CLI, display_name="Runs on this machine", confidence="medium", availability="needs_worker", adapter=adapter, target=str(config.get("cwd") or "") or None, input=InputMode(mode) if mode in InputMode.__members__.values() else InputMode.ARGUMENT, outputs=[OutputMode.STDOUT, OutputMode.FILES], abilities=RuntimeAbilities(background=True, cancel=True, streaming=True, file_artifacts=True), credentials=Credentials(strategy=strategy, names=names))
    if kind == "declared":
        return RuntimeProfile(id="declared", kind=RuntimeKind.UNKNOWN, display_name="Not built yet", confidence="low", availability="not_invocable", adapter=adapter, abilities=RuntimeAbilities(accepts_prompt=False))
    return RuntimeProfile(id="unknown", kind=RuntimeKind.UNKNOWN, display_name=DISPLAY_NAMES[RuntimeKind.UNKNOWN], confidence="low", availability="not_invocable", adapter=adapter, abilities=RuntimeAbilities(accepts_prompt=False))


def ensure_runtimes(db: Session) -> int:
    """Give every provider without profiles one derived from its adapter block. Returns how many."""
    changed = 0
    for provider in db.query(Provider).all():
        if provider.runtimes:
            continue
        rt = runtime_from_adapter(dict(provider.adapter), origin=provider.origin)
        provider.runtimes = [rt.model_dump(mode="json")]
        provider.active_runtime = rt.id
        changed += 1
    if changed:
        db.commit()
    return changed
