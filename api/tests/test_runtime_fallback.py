"""Runtime resilience: pick the best way in at execution time, fall back when a
runtime - not the work - was at fault, and remember what happened.

Every fixture here is generic: providers are built from runtime profiles, not
from any particular agent.
"""

import socket
import threading
import uuid
from datetime import timedelta

import pytest

from adapters import FailureKind, HealthState, InvocationRequest, InvocationResult, ResultState, get_adapter
from adapters.runtime import Credentials, CredentialStrategy, RuntimeHealth, RuntimeKind, RuntimeProfile, credentials_differ, may_fall_back
from app.config import get_settings
from app.connect.runtimes import cli_runtime, http_runtime, rank
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.services import tasks as task_service
from app.services.runtime import _now
from tests import fixture_http_agent
from tests.connect_fixtures import make_script_project


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_profile(rid: str, port: int, **kw) -> RuntimeProfile:
    """An HTTP runtime pointed at a port: alive or dead depending on the fixture."""
    defaults = dict(
        kind=RuntimeKind.PROCESS,
        adapter={"kind": "http", "config": {"base_url": f"http://127.0.0.1:{port}", "health_path": "/health", "invoke": {"method": "POST", "path": "/task", "body": {"prompt": "{request}"}}, "response": {"text": "answer", "summary": "answer", "link": "link"}, "timeout_seconds": 3}},
        display_name="Already running on this machine",
        availability="ready",
        confidence="high",
        credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED),
        evidence=[],
    )
    defaults.update(kw)
    return http_runtime(rid, **defaults)


def cli_profile(rid: str, project, **kw) -> RuntimeProfile:
    defaults = dict(
        kind=RuntimeKind.CLI,
        adapter={"kind": "command", "config": {"argv": ["./bin/agent"], "cwd": str(project), "input": {"mode": "argument"}, "output": {"modes": ["stdout"]}, "timeout_seconds": 30}},
        display_name="Runs from this project",
        availability="needs_worker",
        confidence="high",
        credentials=Credentials(strategy=CredentialStrategy.NONE),
        evidence=[],
    )
    defaults.update(kw)
    return cli_runtime(rid, **defaults)


@pytest.fixture
def two_runtime_provider(seeded, tmp_path, monkeypatch):
    """A provider reachable two ways: a running HTTP service and a local script."""
    monkeypatch.setenv("FIXTURE_UPSTREAM_KEY", "its-own-secret")
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    get_settings.cache_clear()
    project = make_script_project(root, name="fixture-two-ways")
    port = free_port()

    def build(*, serve: bool, cli_credentials: Credentials | None = None):
        server = fixture_http_agent.serve(port) if serve else None
        runtimes = [http_profile("running", port), cli_profile("cli", project, **({"credentials": cli_credentials} if cli_credentials else {}))]
        provider = provider_service.register_provider(seeded, {"name": "Two Ways", "capabilities": [{"id": "research", "title": "Research"}], "adapter": runtimes[0].adapter, "runtimes": runtimes, "active_runtime": "running"})
        seeded.flush()
        return provider, server

    servers: list = []
    yield build, servers
    for server in servers:
        if server is not None:
            server.shutdown()
    get_settings.cache_clear()


def run_work(provider, request_text="what changed?", secrets=None, context=None):
    request = InvocationRequest(task_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), request=request_text, secrets=secrets or {})
    return runtime_service.execute(provider, request, context)


def attempts_of(result) -> list[dict]:
    return (result.metadata or {}).get("attempts") or []


# ----------------------------------------------------------------------------- policy

def test_fallback_policy_is_generic():
    assert may_fall_back(FailureKind.PROVIDER_UNAVAILABLE) and may_fall_back(FailureKind.CONFIGURATION_PROBLEM)
    for kind in (FailureKind.INVOCATION_FAILED, FailureKind.OUTPUT_INVALID, FailureKind.CANCELLED, FailureKind.CREDENTIAL_REQUIRED, None):
        assert not may_fall_back(kind)
    assert not may_fall_back(FailureKind.TIMED_OUT)
    assert may_fall_back(FailureKind.TIMED_OUT, allow_on_timeout=True)
    bevro = Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=["KEY"])
    native = Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED)
    a = RuntimeProfile(id="a", kind=RuntimeKind.CLI, display_name="A", credentials=bevro)
    assert credentials_differ(a, RuntimeProfile(id="b", kind=RuntimeKind.HTTP, display_name="B", credentials=native))
    assert not credentials_differ(a, RuntimeProfile(id="c", kind=RuntimeKind.CLI, display_name="C", credentials=bevro))


def test_health_records_failures_with_a_growing_rest_period():
    health = RuntimeHealth()
    first = health.after_failure("connection refused")
    assert first.state == HealthState.DEGRADED and first.consecutive_failures == 1 and first.in_cooldown()
    second = first.after_failure()
    assert second.state == HealthState.UNAVAILABLE and second.consecutive_failures == 2
    assert second.cooldown_until > first.cooldown_until
    back = second.after_success()
    assert back.state == HealthState.AVAILABLE and back.consecutive_failures == 0 and not back.in_cooldown()


def test_ranking_prefers_a_healthy_runtime_over_a_preferred_but_down_one(tmp_path):
    down = http_profile("running", 1)
    down.health = RuntimeHealth(state=HealthState.UNAVAILABLE, consecutive_failures=2, cooldown_until=_now() + timedelta(minutes=5))
    healthy = cli_profile("cli", tmp_path)
    healthy.health = RuntimeHealth(state=HealthState.AVAILABLE)
    assert [rt.id for rt in rank([down, healthy])] == ["cli", "running"]


# ----------------------------------------------------------------------------- B: healthy preferred runtime

def test_healthy_preferred_runtime_is_used_and_the_alternative_is_untouched(two_runtime_provider, tmp_path):
    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    result, artifacts = run_work(provider)
    assert result.state == ResultState.COMPLETED and result.summary.startswith("Widget answer for:")
    assert result.metadata["runtime"]["id"] == "running"
    assert [(a["runtime_id"], a["outcome"]) for a in attempts_of(result)] == [("running", "completed")]
    assert [a.type for a in artifacts] == ["report", "deep_link"]
    runtime_service.apply_health(provider, result.metadata["runtime_health"])
    states = {rt.id: rt.health.state for rt in runtime_service.runtimes_of(provider)}
    assert states == {"running": HealthState.AVAILABLE, "cli": HealthState.UNKNOWN}  # the CLI was never run


# ----------------------------------------------------------------------------- A: preferred down -> fallback

def test_unavailable_preferred_runtime_falls_back_and_marks_itself_down(two_runtime_provider):
    build, servers = two_runtime_provider
    provider, _ = build(serve=False)  # nothing listens on that port
    result, artifacts = run_work(provider, "what changed in widgets?")

    assert result.state == ResultState.COMPLETED
    assert result.summary == "answering: what changed in widgets?"
    assert result.metadata["runtime"]["id"] == "cli"
    attempts = attempts_of(result)
    assert [(a["runtime_id"], a["outcome"], a["failure_kind"]) for a in attempts] == [("running", "failed", "provider_unavailable"), ("cli", "completed", None)]
    assert attempts[0]["fallback_reason"] == "runtime unavailable" and attempts[0]["contributed"] is False
    assert attempts[1]["contributed"] is True
    # Only the successful attempt's artifacts become the task's.
    assert [a.title for a in artifacts] == ["Output from Two Ways"]

    runtime_service.apply_health(provider, result.metadata["runtime_health"])
    health = {rt.id: rt.health for rt in runtime_service.runtimes_of(provider)}
    assert health["running"].state == HealthState.DEGRADED and health["running"].in_cooldown()
    assert health["cli"].state == HealthState.AVAILABLE

    # The next run goes straight to the healthy one while the other rests.
    usable, skipped = runtime_service.eligible_runtimes(provider)
    assert [rt.id for rt in usable] == ["cli"] and [(rt.id, why) for rt, why in skipped] == [("running", "cooling down after a failure")]


def test_fallback_exhaustion_reports_the_last_real_failure(two_runtime_provider, monkeypatch):
    build, servers = two_runtime_provider
    provider, _ = build(serve=False)
    # The script is there but its folder is not approved, so that way is out too.
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", "")
    result, artifacts = run_work(provider)
    assert result.state == ResultState.FAILED
    assert [a["runtime_id"] for a in attempts_of(result)] == ["running", "cli"]
    assert result.failure == FailureKind.CONFIGURATION_PROBLEM and "could not start" in result.error
    assert len(attempts_of(result)) <= runtime_service.MAX_ATTEMPTS


# ----------------------------------------------------------------------------- D: task-level failure

def test_a_task_level_failure_is_not_retried_elsewhere(two_runtime_provider, monkeypatch):
    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    genuine = InvocationResult(state=ResultState.FAILED, error="Two Ways started but couldn't finish this task.", failure=FailureKind.INVOCATION_FAILED)
    monkeypatch.setattr(get_adapter("http"), "invoke", lambda *a, **k: genuine)
    result, _artifacts = run_work(provider)
    assert result.state == ResultState.FAILED and result.failure == FailureKind.INVOCATION_FAILED
    assert [a["runtime_id"] for a in attempts_of(result)] == ["running"]
    assert attempts_of(result)[0]["fallback_reason"].startswith("the work itself failed")
    assert "runtime_health" not in result.metadata  # nothing was learned about the runtime


# ----------------------------------------------------------------------------- E: cancellation

def test_cancellation_stops_the_run_and_never_falls_back(two_runtime_provider, monkeypatch):
    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    cancelled = InvocationResult(state=ResultState.CANCELLED, error="The task was stopped before completion.", failure=FailureKind.CANCELLED)
    monkeypatch.setattr(get_adapter("http"), "invoke", lambda *a, **k: cancelled)
    result, _artifacts = run_work(provider)
    assert result.state == ResultState.CANCELLED
    assert [a["runtime_id"] for a in attempts_of(result)] == ["running"]
    assert "runtime_health" not in result.metadata


# ----------------------------------------------------------------------------- C: credential-blocked alternative

def test_credential_blocked_alternative_reports_the_credential_not_a_vague_failure(two_runtime_provider):
    build, servers = two_runtime_provider
    provider, _ = build(serve=False, cli_credentials=Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=["FIXTURE_KEY"], required_from_user=True))
    result, artifacts = run_work(provider)
    assert result.state == ResultState.FAILED and result.failure == FailureKind.CREDENTIAL_REQUIRED
    assert result.metadata["missing_secrets"] == ["FIXTURE_KEY"]
    assert artifacts == []
    # With the credential in hand the same provider falls back to the script and works.
    result, _ = run_work(provider, secrets={"FIXTURE_KEY": "value"})
    assert result.state == ResultState.COMPLETED and result.metadata["runtime"]["id"] == "cli"


def test_a_credential_failure_only_falls_back_to_a_different_strategy(two_runtime_provider, monkeypatch):
    build, servers = two_runtime_provider
    same = Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=["KEY"], required_from_user=False)
    provider, _ = build(serve=True, cli_credentials=same)
    runtimes = runtime_service.runtimes_of(provider)
    runtimes[0].credentials = same  # both ways want the very same secret
    runtime_service.set_runtimes(provider, runtimes, "running")
    monkeypatch.setattr(get_adapter("http"), "invoke", lambda *a, **k: InvocationResult(state=ResultState.FAILED, error="didn't accept Bevro's credential.", failure=FailureKind.CREDENTIAL_REQUIRED))
    result, _ = run_work(provider, secrets={"KEY": "wrong"})
    assert result.state == ResultState.FAILED and result.failure == FailureKind.CREDENTIAL_REQUIRED
    outcomes = [(a["runtime_id"], a["outcome"]) for a in attempts_of(result)]
    assert outcomes == [("running", "failed"), ("cli", "skipped")]
    assert attempts_of(result)[1]["fallback_reason"].startswith("same credential strategy")


# ----------------------------------------------------------------------------- recovery

def test_a_rested_runtime_recovers_after_a_successful_test(two_runtime_provider):
    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    runtimes = runtime_service.runtimes_of(provider)
    runtimes[0].health = RuntimeHealth(state=HealthState.UNAVAILABLE, consecutive_failures=2, cooldown_until=_now() + timedelta(minutes=10))
    runtime_service.set_runtimes(provider, runtimes, "running")
    assert [rt.id for rt in runtime_service.eligible_runtimes(provider)[0]] == ["cli"]

    outcomes, deferred = runtime_service.check_all_runtimes(provider, {})
    assert {rt.id: result.ok for rt, result in outcomes} == {"running": True, "cli": True} and deferred == []
    health = {rt.id: rt.health for rt in runtime_service.runtimes_of(provider)}
    assert health["running"].state == HealthState.AVAILABLE and not health["running"].in_cooldown()
    assert [rt.id for rt in runtime_service.eligible_runtimes(provider)[0]] == ["running", "cli"]


def test_cooldown_expiry_lets_a_runtime_be_tried_again(two_runtime_provider):
    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    runtimes = runtime_service.runtimes_of(provider)
    runtimes[0].health = RuntimeHealth(state=HealthState.DEGRADED, consecutive_failures=1, cooldown_until=_now() - timedelta(seconds=1))
    runtime_service.set_runtimes(provider, runtimes, "running")
    usable, skipped = runtime_service.eligible_runtimes(provider)
    assert "running" in [rt.id for rt in usable] and skipped == []
    result, _ = run_work(provider)
    assert result.state == ResultState.COMPLETED and result.metadata["runtime"]["id"] == "running"


# ----------------------------------------------------------------------------- through a ProviderRun

def test_one_provider_run_holds_the_attempt_history_and_no_duplicate_artifacts(client, seeded, two_runtime_provider):
    build, servers = two_runtime_provider
    provider, _ = build(serve=False)
    provider_service.record_availability(seeded, provider, __import__("adapters", fromlist=["HealthResult"]).HealthResult(ok=True, state="available"))

    task = task_service.submit(seeded, "Two Ways: what changed?", provider=provider)
    seeded.commit()
    run = task.runs[-1]
    request = task_service.build_request(seeded, run, secret_store=None)
    result, artifacts = task_service.execute(run.provider, request)
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result, artifacts)

    assert task.state == "completed" and len(task.runs) == 1  # one piece of work, whatever it took
    assert [a.title for a in task.artifacts] == ["Output from Two Ways"]
    assert [a["runtime_id"] for a in run.meta["attempts"]] == ["running", "cli"]
    assert run.meta["runtime"]["id"] == "cli"
    # Health landed on the provider, not on the run.
    assert "runtime_health" not in run.meta
    assert {rt.id: rt.health.state for rt in runtime_service.runtimes_of(provider)}["running"] == HealthState.DEGRADED

    detail = client.get(f"/api/tasks/{task.id}").json()
    assert detail["state"] == "completed" and detail["runs"][-1]["recovered"] is True
    assert "runtime" not in str(detail["runs"][-1]) and "attempts" not in str(detail)
    for word in ("http", "cli", "127.0.0.1", "argv", "cooldown"):
        assert word not in str(detail).lower().replace("https://desk.example", ""), word


def test_the_router_still_chooses_providers_only(seeded, two_runtime_provider):
    from app.routing.catalogue import build_catalogue

    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    provider_service.record_availability(seeded, provider, __import__("adapters", fromlist=["HealthResult"]).HealthResult(ok=True, state="available"))
    entry = next(e for e in build_catalogue(seeded) if e.id == provider.slug)
    text = str(entry.model_dump())
    for word in ("runtime", "http", "cli", "127.0.0.1", "fallback"):
        assert word not in text.lower(), word


def test_the_worker_path_falls_back_the_same_way(seeded, two_runtime_provider, monkeypatch):
    """The worker drives a run through the same runtime service as the API."""
    from adapters import HealthResult, InvocationContext
    from app import worker as worker_module

    build, servers = two_runtime_provider
    provider, _ = build(serve=False)
    provider_service.record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    runtimes = runtime_service.runtimes_of(provider)
    runtimes[0].abilities = runtimes[0].abilities.model_copy(update={"background": True})
    runtime_service.set_runtimes(provider, runtimes, "running")

    task = task_service.submit(seeded, "Two Ways: what changed?", provider=provider)
    seeded.commit()
    run = task.runs[-1]

    phases: list[str] = []
    request = task_service.build_request(seeded, run, secret_store=None)
    result, artifacts = task_service.execute(run.provider, request, InvocationContext(progress=phases.append))
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result, artifacts)

    assert task.state == "completed" and run.meta["runtime"]["id"] == "cli"
    assert [a["runtime_id"] for a in run.meta["attempts"]] == ["running", "cli"]
    assert phases  # progress came from the runtime that actually ran
    assert callable(worker_module.task_service.execute)  # the worker uses this same entry point


def test_manage_shows_the_alternatives_without_mechanism(client, seeded, two_runtime_provider):
    from adapters import HealthResult

    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    provider_service.record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    listed = next(p for p in client.get("/api/providers").json() if p["slug"] == provider.slug)
    assert listed["runtime"]["display_name"] == "Already running on this machine"
    assert listed["runtime"]["alternatives"] == 1 and listed["runtime"]["health"] == "unknown"
    for word in ("http", "argv", "127.0.0.1", "adapter"):
        assert word not in str(listed["runtime"]).lower()

    r = client.post(f"/api/providers/{provider.id}/check")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "All 2 ways work" in r.json()["detail"]
    # The API tests what it can reach itself; a way that lives on the host is
    # the worker's to report, so Bevro does not claim to have tested it.
    health = {rt.id: rt.health.state for rt in runtime_service.runtimes_of(provider)}
    assert health == {"running": HealthState.AVAILABLE, "cli": HealthState.UNKNOWN}

    details = client.get(f"/api/providers/{provider.id}/details").json()
    assert [(d["id"], d["kind"], d["health"]) for d in details["runtimes"]] == [("running", "process", "available"), ("cli", "cli", "unknown")]


def test_a_run_never_tries_a_runtime_the_process_cannot_reach(seeded, two_runtime_provider):
    """The API cannot reach a folder on the host; the worker can reach everything,
    so a provider with any host runtime is handed to the worker."""
    build, servers = two_runtime_provider
    provider, server = build(serve=True)
    servers.append(server)
    inline, skipped = runtime_service.eligible_runtimes(provider, execution="inline")
    assert [rt.id for rt in inline] == ["running"]
    assert [(rt.id, why) for rt, why in skipped] == [("cli", "needs the worker on the host")]
    assert [rt.id for rt in runtime_service.eligible_runtimes(provider, execution="background")[0]] == ["running", "cli"]
    # With a worker about, the whole run goes there: it can fall back between both.
    assert runtime_service.execution_of(provider, True) == "background"
    # With no worker, the network way is still driven by the API rather than wait.
    assert runtime_service.execution_of(provider, False) == "inline"
    assert runtime_service.reachable_without_worker(provider)
    assert provider_service.availability_of(provider)["state"] == "available"

    task = task_service.submit(seeded, "Two Ways: what changed?", provider=provider)
    seeded.commit()
    assert task.runs[-1].execution == "inline"  # no worker has reported in this test
    run = task.runs[-1]
    request = task_service.build_request(seeded, run, secret_store=None)
    result, artifacts = task_service.execute(run.provider, request, execution=run.execution)
    assert result.state == ResultState.COMPLETED and result.metadata["runtime"]["id"] == "running"
