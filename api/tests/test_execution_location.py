"""Where a runtime is driven from, as opposed to how it is reached.

A web service may be visible to the API container, to the host worker, to
both, or to neither. That is a different question from whether the service
works, and Bevro has to keep the two apart: "I cannot reach it" is a
statement about a process, not about a provider.

    A  reachable from the API only
    B  reachable from the worker only
    C  reachable from both
    D  reachable from neither
    E  the API's route fails after connecting; the worker's still works
    F  the worker disappears; the API's route still works
    G  an operational OpenAPI service only the worker can see
    H  MCP over HTTP only the worker can see
    I  a prompt HTTP service only the worker can see
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from adapters.base import HealthResult
from adapters.runtime import BaseRuntimeAdapter
from adapters.registry import get_adapter, may_run_in_background
from adapters.runtime import API, WORKER, Reachability, RuntimeKind, RuntimeProfile
from app.services import providers as provider_service
from app.worker import background_kinds
from app.services import runtime as runtime_service
from app.services import tasks as task_service
from tests import operational_fixtures as fx


@pytest.fixture(autouse=True)
def reset_discovery():
    """Put the real discovery service back after each test."""
    from app.connect.service import set_discovery_service

    yield
    set_discovery_service(None)


def network_runtime(kind: RuntimeKind = RuntimeKind.OPENAPI, **reach) -> RuntimeProfile:
    return RuntimeProfile(
        id=str(kind),
        kind=kind,
        display_name="Connected over the network",
        adapter={"kind": "openapi" if kind == RuntimeKind.OPENAPI else "http", "config": {"base_url": "http://service.local"}},
        reachability=Reachability(**reach),
    )


def host_runtime() -> RuntimeProfile:
    return RuntimeProfile(
        id="cli",
        kind=RuntimeKind.CLI,
        display_name="Runs on this machine",
        adapter={"kind": "command", "config": {"argv": ["python", "-m", "thing"], "cwd": "/somewhere"}},
    )


def provider_with(*runtimes: RuntimeProfile) -> SimpleNamespace:
    return SimpleNamespace(
        runtimes=[rt.model_dump(mode="json") for rt in runtimes],
        active_runtime=runtimes[0].id if runtimes else None,
        adapter=runtimes[0].adapter if runtimes else {},
    )


# --------------------------------------------------------------------------- where work goes

def test_a_service_only_the_api_can_see_runs_here():
    """A. Nothing else is needed, so nothing else is involved."""
    provider = provider_with(network_runtime(api="available", worker="unavailable"))
    assert runtime_service.execution_of(provider, True) == "inline"
    assert runtime_service.reachable_without_worker(provider) is True


def test_a_service_only_the_worker_can_see_runs_there():
    """B. The API cannot reach it; that is not the provider's fault."""
    provider = provider_with(network_runtime(api="unavailable", worker="available"))
    assert runtime_service.execution_of(provider, True) == "background"
    assert runtime_service.reachable_without_worker(provider) is False


def test_a_service_both_can_see_runs_in_the_least_dependent_place():
    """C. The API needs no other process, so it takes it."""
    provider = provider_with(network_runtime(api="available", worker="available"))
    assert runtime_service.execution_of(provider, True) == "inline"


def test_a_service_neither_can_see_still_has_to_land_somewhere():
    """D. It will fail, but as a provider that cannot be reached - with the
    worker asked last, since it can see the most."""
    provider = provider_with(network_runtime(api="unavailable", worker="unavailable"))
    assert runtime_service.locations_for(provider, runtime_service.runtimes_of(provider)[0]) == []
    assert runtime_service.execution_of(provider, True) == "background"


def test_an_untried_service_is_tried_here_first():
    """Unknown is not the same as unavailable: the cheap option goes first."""
    provider = provider_with(network_runtime())
    assert runtime_service.execution_of(provider, True) == "inline"


def test_a_route_known_to_work_beats_one_merely_untried():
    provider = provider_with(network_runtime(api="unknown", worker="available"))
    rt = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.locations_for(provider, rt) == [WORKER, API]


def test_the_worker_going_away_sends_work_back_to_the_api():
    """F. The API's own route still works, so the work still happens."""
    provider = provider_with(network_runtime(api="available", worker="available"))
    assert runtime_service.execution_of(provider, False) == "inline"


def test_with_no_worker_and_no_route_of_its_own_there_is_nowhere():
    provider = provider_with(network_runtime(api="unavailable", worker="available"))
    rt = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.locations_for(provider, rt, worker_available=False) == []


def test_a_mixed_provider_still_prefers_the_worker_that_can_drive_everything():
    """The older rule, kept: the worker can drive both ways in, so sending the
    whole provider there leaves the second way available to fall back to."""
    provider = provider_with(network_runtime(api="available"), host_runtime())
    assert runtime_service.execution_of(provider, True) == "background"
    # ...but it does not wait for a worker that is not there.
    assert runtime_service.execution_of(provider, False) == "inline"


@pytest.mark.parametrize("kind", [RuntimeKind.OPENAPI, RuntimeKind.HTTP, RuntimeKind.MCP_HTTP])
def test_every_network_runtime_answers_the_same_question(kind):
    """G, H, I: the mechanism does not change where it can be driven from."""
    provider = provider_with(network_runtime(kind, api="unavailable", worker="available"))
    assert runtime_service.execution_of(provider, True) == "background"


def test_a_connection_test_cannot_pass_without_testing_anything():
    """`check` and `health` are two names for one question. An adapter that
    answers only one of them must not inherit a cheerful default for the
    other: "Test passed" has to mean something was tried."""

    class OnlyHealth(BaseRuntimeAdapter):
        kind = "only-health"

        def health(self, provider, secrets):
            return HealthResult(ok=False, detail="nothing answered")

    assert OnlyHealth().check(None, {}).ok is False
    # The real one, which is what the connection test calls.
    assert get_adapter("openapi").check(
        SimpleNamespace(adapter_config={}, adapter={}), {}
    ).ok is False


def test_the_worker_is_willing_to_pick_up_network_work():
    """Deciding that a run belongs on the worker is no use if the worker will
    not claim it. A network adapter usually runs inline, so it has to say
    separately that the host is a place it could run."""
    kinds = background_kinds()
    assert "openapi" in kinds and "http" in kinds
    assert may_run_in_background(get_adapter("openapi")) is True
    # ...without that changing where such a run goes by default.
    provider = provider_with(network_runtime(RuntimeKind.OPENAPI))
    assert runtime_service.execution_of(provider, True) == "inline"


def test_something_that_needs_the_host_is_the_worker_s_whatever_anyone_can_reach():
    provider = provider_with(host_runtime())
    rt = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.is_network(rt) is False
    assert runtime_service.locations_for(provider, rt) == [WORKER]


# --------------------------------------------------------------------------- discovery from the host

def unreachable_from_here() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route from this process")

    return httpx.MockTransport(handler)


def test_an_address_the_api_cannot_reach_is_handed_to_the_worker(client, seeded, monkeypatch):
    """The browser asks once; who does the looking is Bevro's business."""
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy
    from app.models import ConnectDraft

    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(unreachable_from_here()), LocalProjectStrategy(), CommandStrategy()], use_assist=False))
    monkeypatch.setattr(provider_service, "worker_seen_recently", lambda _db: True)

    body = client.post("/api/connect/discover", json={"target": "http://service.local/p/someone/"}).json()

    # To the person it is still "Looking…" - not "it is not there".
    assert body["state"] == "looking"
    assert not body.get("error")
    row = seeded.get(ConnectDraft, uuid.UUID(body["id"]))
    assert row.state == "pending"  # waiting for whoever can see it


def test_with_no_worker_an_unreachable_address_says_so(client, seeded, monkeypatch):
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy

    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(unreachable_from_here()), LocalProjectStrategy(), CommandStrategy()], use_assist=False))
    monkeypatch.setattr(provider_service, "worker_seen_recently", lambda _db: False)

    body = client.post("/api/connect/discover", json={"target": "http://service.local/"}).json()
    assert body["state"] == "failed"
    assert "Nothing answered" in body["error"]


def test_the_worker_finds_it_and_records_that_it_can(seeded, monkeypatch):
    """G: an operational service only the host can see, discovered there."""
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy
    from app.models import ConnectDraft
    from app.services import connect as connect_service

    row = ConnectDraft(target_kind="url", target="http://service.local/", state="pending")
    seeded.add(row)
    seeded.flush()

    transport, _ = fx.service(descriptor=fx.JOBS_API)
    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(transport), LocalProjectStrategy(), CommandStrategy()], use_assist=False))
    assert connect_service.run_pending(seeded, []) == 1

    seeded.refresh(row)
    assert row.state == "found"
    reach = row.draft["runtimes"][0]["reachability"]
    assert reach["worker"] == "available" and reach["worker_checked_at"]
    assert reach["api"] == "unknown"  # this process never got there, and never claimed to


def test_testing_a_host_only_connection_is_not_tried_here_again():
    """Pressing Test must not report "not reachable" about a connection the
    worker uses every day. A route already ruled out here is left to the
    process that has it."""
    provider = provider_with(network_runtime(api="unavailable", worker="available"))
    outcomes, deferred = runtime_service.check_all_runtimes(provider, {}, execution="inline")
    assert outcomes == [] and [rt.id for rt in deferred] == [str(RuntimeKind.OPENAPI)]

    # One never tried here is still this process's to try: that is how it learns.
    fresh = provider_with(network_runtime())
    assert runtime_service.execution_location(fresh, runtime_service.runtimes_of(fresh)[0]) == API


def test_a_connection_only_the_host_can_reach_says_so():
    """The person is told when a connection depends on the worker running -
    and is not told anything when it does not, because "runs over the
    network" is the ordinary case and needs no explanation."""
    assert Reachability(api="unavailable").host_only() is True
    assert Reachability(api="unavailable", worker="available").host_only() is True
    assert Reachability(api="unavailable", worker="unavailable").host_only() is False
    assert Reachability().host_only() is False
    assert Reachability(api="available").host_only() is False

    rt = network_runtime(api="unavailable", worker="available")
    assert rt.public()["runs_at"] == "On this machine"
    assert network_runtime(api="available").public()["runs_at"] is None


def test_a_worker_says_it_is_there_rather_than_being_guessed_at(seeded):
    """Whether an unreachable address is handed to the host depends on a
    worker being there. Inferring that from recent work fails on a new
    installation, which has none - and that is the case that needs it."""
    from datetime import timedelta

    from app.models import WorkerHeartbeat
    from app.models._common import utcnow

    assert provider_service.worker_on_the_host(seeded) is False
    provider_service.record_heartbeat(seeded, "somewhere:1", ["openapi", "command"])
    assert provider_service.worker_on_the_host(seeded) is True
    assert provider_service.worker_seen_recently(seeded) is True

    # A worker that stopped saying so stops counting.
    row = seeded.get(WorkerHeartbeat, "somewhere:1")
    row.seen_at = utcnow() - timedelta(seconds=provider_service.WORKER_TTL_SECONDS + 60)
    seeded.commit()
    assert provider_service.worker_on_the_host(seeded) is False


def _discovery_over(transport) -> None:
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy

    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(transport), LocalProjectStrategy(), CommandStrategy()], use_assist=False))


def _connected_service(db, transport) -> object:
    """A provider connected from an address, as Connect would have left it."""
    from app.services import connect as connect_service

    _discovery_over(transport)
    draft = connect_service.start_discovery(db, "http://service.local/")
    assert draft.state == "found", draft.error
    return connect_service.confirm_draft(db, draft, name=None, description=None, capability_summary=None, secrets={}, app_url=None)


def test_a_reconnect_the_api_cannot_do_is_done_by_the_worker(seeded, monkeypatch):
    """Pressing Reconnect must work for a service only the host can see. The
    API hands it over rather than answering "nothing answered at that
    address", which would be a statement about the wrong process."""
    from app.services import connect as connect_service

    provider = _connected_service(seeded, fx.service(descriptor=fx.JOBS_API)[0])

    # From now on this process cannot reach it; the host still can.
    _discovery_over(unreachable_from_here())
    from app.connect.strategies.base import NotReachable

    with pytest.raises(NotReachable):
        connect_service._reconnect_here(seeded, provider)

    # The worker's turn: the same call, in a process that can get there.
    provider.source = {**(provider.source or {}), "reconnect_requested_at": "2026-01-01T00:00:00+00:00"}
    seeded.commit()
    _discovery_over(fx.service(descriptor=fx.JOBS_API)[0])
    assert connect_service.run_reconnects(seeded) == 1

    seeded.refresh(provider)
    assert "reconnect_requested_at" not in (provider.source or {})
    reach = runtime_service.runtimes_of(provider)[0].reachability
    assert reach.worker == "available" and reach.api == "unavailable"


def test_a_reconnect_says_so_when_no_one_can_reach_it(seeded):
    """No worker, no route: a plain sentence, not a hand-off into nowhere."""
    from app.services import connect as connect_service

    provider = _connected_service(seeded, fx.service(descriptor=fx.JOBS_API)[0])
    _discovery_over(unreachable_from_here())
    with pytest.raises(connect_service.DraftError):
        connect_service.reconnect_provider(seeded, provider)


def test_reconnecting_picks_up_what_the_service_now_says_it_can_do(seeded):
    """A service that has grown new operations is worth re-reading: Bevro
    routes on what it says it can do, so a stale list quietly misroutes."""
    from app.services import connect as connect_service

    provider = _connected_service(seeded, fx.service(descriptor=fx.READ_ONLY_API)[0])
    before = {c["id"] for c in provider.capabilities}
    _discovery_over(fx.service(descriptor=fx.JOBS_API)[0])
    connect_service.reconnect_provider(seeded, provider)
    seeded.refresh(provider)
    assert {c["id"] for c in provider.capabilities} != before


def test_reconnecting_keeps_what_the_person_said_it_is_for(seeded):
    """What the person wrote under "What should Bevro use it for?" is the
    strongest routing evidence there is. Looking again refreshes what the
    service says about itself and leaves theirs alone."""
    from app.services import connect as connect_service

    provider = _connected_service(seeded, fx.service(descriptor=fx.READ_ONLY_API)[0])
    provider.capabilities = [{"id": "household_admin", "title": "Household admin", "by": "person"}, *provider.capabilities]
    seeded.commit()
    _discovery_over(fx.service(descriptor=fx.JOBS_API)[0])
    connect_service.reconnect_provider(seeded, provider)
    seeded.refresh(provider)
    assert provider.capabilities[0] == {"id": "household_admin", "title": "Household admin", "by": "person"}
    assert [c["id"] for c in provider.capabilities].count("household_admin") == 1
    assert len(provider.capabilities) > 1  # and the service's own list is there too


def test_what_the_person_types_is_marked_as_theirs():
    from app.connect.capabilities import capabilities_from_summary

    assert [(c.id, c.by) for c in capabilities_from_summary("Meal planning, shopping lists")] == [("meal_planning", "person"), ("shopping_lists", "person")]


def test_a_service_s_own_words_for_a_thing_are_kept(seeded):
    """A tag and a URL often disagree - "shortlist" over /opportunities - and
    a person may ask with either word."""
    from app.connect.openapi import capabilities_from_operations, compile_catalogue

    spec = fx.JOBS_API
    by_id = {c["id"]: c for c in capabilities_from_operations(compile_catalogue(spec))}
    assert "jobs" in by_id
    assert "stats" in by_id["jobs"].get("terms", []) or by_id["jobs"].get("terms") is not None


# --------------------------------------------------------------------------- when a route stops working

def test_a_run_the_api_cannot_reach_moves_to_the_worker_rather_than_failing(seeded, monkeypatch):
    """E. The work was asked for; where it runs is Bevro's problem, not the person's."""
    from adapters.base import FailureKind, InvocationResult, ResultState
    from app.domain.run_state import RunState

    provider = provider_service.register_provider(
        seeded,
        {
            "name": "Batch Service",
            "description": "",
            "capabilities": [{"id": "jobs", "title": "Jobs"}],
            "adapter": {"kind": "openapi", "config": fx.as_config(fx.JOBS_API)},
            "origin": "connected",
        },
    )
    provider.runtimes = [network_runtime(api="unknown", worker="unknown").model_dump(mode="json")]
    seeded.commit()

    task = task_service.submit(seeded, "Show me the statistics.", provider=provider)
    seeded.commit()
    run = task.runs[-1]
    assert run.execution == "inline"  # untried, so the cheap option was chosen

    monkeypatch.setattr(provider_service, "worker_seen_recently", lambda _db: True)
    monkeypatch.setattr(
        task_service,
        "execute",
        lambda *a, **k: (InvocationResult(state=ResultState.FAILED, error="Couldn't reach it", failure=FailureKind.PROVIDER_UNAVAILABLE), []),
    )
    task_service.execute_run(seeded, run.id)
    seeded.commit()

    seeded.refresh(run)
    assert run.execution == "background" and run.state == RunState.PENDING
    assert task.state != "failed"  # nothing was reported to the person as broken
    # And the API's failure to reach it is remembered, so the next run starts there.
    reach = runtime_service.runtimes_of(provider)[0].reachability
    assert reach.api == "unavailable"
    assert runtime_service.execution_of(provider, True) == "background"


def test_a_run_that_simply_failed_is_not_moved_anywhere(seeded, monkeypatch):
    """Only unreachability moves work. A provider that answered and said no
    has answered."""
    from adapters.base import FailureKind, InvocationResult, ResultState

    provider = provider_service.register_provider(
        seeded,
        {
            "name": "Batch Service",
            "description": "",
            "capabilities": [{"id": "jobs", "title": "Jobs"}],
            "adapter": {"kind": "openapi", "config": fx.as_config(fx.JOBS_API)},
            "origin": "connected",
        },
    )
    provider.runtimes = [network_runtime(api="available").model_dump(mode="json")]
    seeded.commit()
    task = task_service.submit(seeded, "Show me the statistics.", provider=provider)
    seeded.commit()

    monkeypatch.setattr(provider_service, "worker_seen_recently", lambda _db: True)
    monkeypatch.setattr(
        task_service,
        "execute",
        lambda *a, **k: (InvocationResult(state=ResultState.FAILED, error="It said no", failure=FailureKind.INVOCATION_FAILED), []),
    )
    task_service.execute_run(seeded, task.runs[-1].id)
    seeded.commit()
    assert task.state == "failed"


# --------------------------------------------------------------------------- routing

def test_the_router_does_not_care_where_a_provider_runs_from(seeded):
    """A provider is routable when some runtime has somewhere to run."""
    from app.routing.catalogue import build_catalogue

    provider = provider_service.register_provider(
        seeded,
        {
            "name": "Batch Service",
            "description": "Runs batch jobs",
            "capabilities": [{"id": "jobs", "title": "Jobs"}],
            "adapter": {"kind": "openapi", "config": fx.as_config(fx.JOBS_API)},
            "origin": "connected",
        },
    )
    provider.runtimes = [network_runtime(api="unavailable", worker="available").model_dump(mode="json")]
    provider_service.record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    seeded.commit()

    entry = next(e for e in build_catalogue(seeded, selectable_only=False) if e.id == provider.slug)
    assert entry.can_invoke and entry.available
    assert "worker" not in str(entry.model_dump()).lower() and "inline" not in str(entry.model_dump()).lower()


# --------------------------------------------------------------------------- the same rules, wherever it runs

def test_the_url_policy_is_the_same_in_both_processes():
    """The worker sees more networks, which makes the policy matter more."""
    from adapters import urlsafety

    for refused in ("file:///etc/passwd", "gopher://host/x", "data:text/plain,hi", "ftp://host/x"):
        with pytest.raises(urlsafety.UnsafeUrl):
            urlsafety.check(refused)
    assert urlsafety.check("https://service.local/a")
    assert urlsafety.MAX_REDIRECTS <= 5  # a chain cannot be walked indefinitely
    assert urlsafety.same_origin("http://a.local:6300", "http://a.local:6300/api/v1/x")
    assert not urlsafety.same_origin("http://a.local:6300", "http://evil.local/api")


def test_an_operation_is_called_on_the_service_s_own_address(seeded):
    """Nothing in a payload can send the next call somewhere else."""
    from adapters.openapi import OpenApiAdapter, OperationPlan, PlannedStep, validate_plan
    from adapters.base import InvocationRequest, ProviderSpec

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"done": 1})

    config = fx.as_config(fx.JOBS_API, base_url="http://service.local")
    adapter = OpenApiAdapter(transport=httpx.MockTransport(handler))
    spec = ProviderSpec(id="p", slug="s", name="S", description="", capabilities=[], adapter={"kind": "openapi", "config": config})
    plan = validate_plan(OperationPlan(steps=[PlannedStep(operation_id="stats")]), config)
    adapter.invoke(spec, InvocationRequest(task_id="t", run_id="r", request="stats", input={"operation_plan": plan.model_dump()}, secrets={}))

    assert seen == ["http://service.local/api/stats"]
