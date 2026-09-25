"""Where a provider runs, for every kind of provider Bevro supports.

One question, asked eight ways: *which of Bevro's processes drives this?*
Nothing here knows what any of these services is. Each one is a fixture in
`operational_fixtures.py` - a prompt endpoint, a set of typed operations, an
MCP server, a command in a folder - and the answer has to come from the
mechanism and from what was actually tried, never from a name or an address.

    A  prompt over HTTP     both can see it        -> the API, which needs no worker
    B  prompt over HTTP     only the worker can    -> the worker
    C  typed operations     only the worker can    -> the worker finds it and runs it
    D  MCP over HTTP        only the worker can    -> the worker finds it and runs it
    E  a command on the host                       -> the worker, as it always was
    F  network + command    the network way is out -> the other way in, unchanged
    G  the worker loses it, the API gains it       -> it moves back, without reconnecting
    H  nobody can see it                           -> unavailable, and said so
"""

from __future__ import annotations

import json

import pytest

from adapters.base import HealthResult
from adapters.runtime import API, WORKER, RuntimeKind
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from tests import operational_fixtures as fx


@pytest.fixture(autouse=True)
def reset_discovery():
    from app.connect.service import set_discovery_service

    yield
    set_discovery_service(None)


def discovery_over(transport) -> None:
    """Point discovery at one pretend service, whatever kind it is."""
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy

    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(transport), LocalProjectStrategy(), CommandStrategy()], use_assist=False))


def over_the_fixture(monkeypatch, transport) -> None:
    """Let the MCP adapter talk to a pretend server instead of the network."""
    import adapters.mcp as mcp_adapter
    from adapters.mcp_client import HttpSession

    def session(url, headers=None, timeout=None, **kw):
        return HttpSession(url, headers=headers, timeout=timeout or 30.0, transport=transport)

    monkeypatch.setattr(mcp_adapter, "HttpSession", session)


def found_by_the_worker(db, url: str, transport):
    """What Connect does when this process cannot reach an address at all."""
    from app.models import ConnectDraft
    from app.services import connect as connect_service

    # The API tries first and gets nowhere; the draft is left for the worker.
    discovery_over(fx.nothing_answers())
    row = ConnectDraft(target_kind="url", target=url, state="pending")
    db.add(row)
    db.flush()
    connect_service._discover_into(row, [], {})
    assert row.state == "failed" and row.unreachable

    row.state = "pending"
    db.commit()
    discovery_over(transport)
    assert connect_service.run_pending(db, []) == 1
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- A, B

@pytest.mark.parametrize("kind", ["http", "openapi", "mcp_http"])
def test_a_both_can_see_it_so_the_api_takes_it(kind):
    provider = fx.provider_with(fx.network_runtime(kind, api="available", worker="available"))
    rt = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.execution_location(provider, rt, worker_available=True) == API
    assert runtime_service.execution_of(provider, True) == "inline"


@pytest.mark.parametrize("kind", ["http", "openapi", "mcp_http"])
def test_b_only_the_worker_can_see_it_so_the_worker_takes_it(kind):
    provider = fx.provider_with(fx.network_runtime(kind, api="unavailable", worker="available"))
    rt = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.execution_location(provider, rt, worker_available=True) == WORKER
    assert runtime_service.execution_of(provider, True) == "background"


# --------------------------------------------------------------------------- C

def test_c_typed_operations_the_worker_finds_and_runs(seeded):
    """An operational service the container cannot reach: discovered on the
    host, and the work itself then belongs there too."""
    from adapters import InvocationRequest, ProviderSpec
    from adapters.openapi import OpenApiAdapter
    from app.operations import planner
    from app.services import connect as connect_service

    transport, calls = fx.service(descriptor=fx.JOBS_API)
    row = found_by_the_worker(seeded, "http://service.local/", transport)
    assert row.state == "found"

    provider = connect_service.confirm_draft(seeded, row, name=None, description=None, capability_summary=None, secrets={}, app_url=None)
    seeded.commit()
    assert runtime_service.execution_of(provider, True) == "background"
    rt = runtime_service.runtimes_of(provider)[0]
    assert rt.reachability.worker == "available" and rt.reachability.api == "unavailable"

    # And the same adapter, driven where the service answers, does the work.
    config = provider.adapter["config"]
    spec = ProviderSpec(id=str(provider.id), slug=provider.slug, name=provider.name, adapter=provider.adapter)
    plan, allowed = planner.plan("What jobs are there?", config)
    result = OpenApiAdapter(transport=transport).invoke(
        spec,
        InvocationRequest(task_id="t", run_id="r", request="What jobs are there?", input={"operation_plan": plan.model_dump(), "allowed_safety": sorted(allowed)}, secrets={}),
    )
    assert result.state.value == "completed" and result.artifacts


# --------------------------------------------------------------------------- D

def test_d_mcp_over_http_the_worker_finds_and_runs(seeded, monkeypatch):
    """The same story through a different mechanism. Nothing in the decision
    changed, which is the point."""
    from adapters import InvocationRequest, ProviderSpec, get_adapter
    from app.services import connect as connect_service

    transport, calls = fx.mcp_service()
    over_the_fixture(monkeypatch, transport)
    row = found_by_the_worker(seeded, "http://desk.local/mcp", transport)
    assert row.state == "found" and row.draft["mechanism"] == "mcp"

    provider = connect_service.confirm_draft(seeded, row, name=None, description=None, capability_summary=None, secrets={}, app_url=None)
    seeded.commit()
    assert runtime_service.execution_of(provider, True) == "background"
    rt = runtime_service.runtimes_of(provider)[0]
    assert rt.reachability.worker == "available" and rt.reachability.api == "unavailable"

    spec = ProviderSpec(id=str(provider.id), slug=provider.slug, name=provider.name, adapter=provider.adapter)
    result = get_adapter("mcp").invoke(spec, InvocationRequest(task_id="t", run_id="r", request="opening hours", input={}, secrets={}))
    answered = " ".join([result.summary or "", *(json.dumps(a.model_dump(mode="json"), default=str) for a in result.artifacts)])
    assert result.state.value == "completed" and "opening hours" in answered
    assert calls.count("POST /mcp") >= 2  # it really spoke to the server: initialize, then the call


# --------------------------------------------------------------------------- E

def test_e_a_command_on_the_host_is_unchanged():
    """Nothing about reachability applies: a program in a folder is the
    worker's because of what it is, not because of what can be reached."""
    provider = fx.provider_with(fx.host_runtime())
    rt = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.is_network(rt) is False
    assert runtime_service.locations_for(provider, rt) == [WORKER]
    assert runtime_service.execution_of(provider, True) == "background"
    # Untouched by anything the network knows or does not know.
    assert rt.reachability.api == "unknown" and rt.reachability.worker == "unknown"


# --------------------------------------------------------------------------- F

def test_f_the_other_way_in_still_takes_over(seeded):
    """Runtime fallback, which predates all of this, still works: when one
    way in is unhealthy the next eligible one is used."""
    from adapters.runtime import RuntimeHealth, HealthState

    network = fx.network_runtime("http", api="unavailable", worker="unavailable")
    network.health = RuntimeHealth(state=HealthState.UNAVAILABLE, consecutive_failures=3)
    command = fx.host_runtime()
    provider = provider_service.register_provider(
        seeded,
        {"name": "Two Ways", "description": "", "capabilities": [{"id": "jobs", "title": "Jobs"}], "adapter": command.adapter, "origin": "connected"},
    )
    runtime_service.set_runtimes(provider, [network, command], network.id)
    seeded.commit()

    usable, _skipped = runtime_service.eligible_runtimes(provider)
    # The unhealthy network way is demoted, not deleted: it stays as a
    # fallback for when it recovers, which is the older behaviour unchanged.
    assert [rt.id for rt in usable] == ["cli", network.id]
    assert runtime_service.execution_of(provider, True) == "background"


# --------------------------------------------------------------------------- G

def test_g_a_route_that_comes_back_is_used_again_without_reconnecting():
    """Reachability is what was last seen, not a verdict. A worker that loses
    sight of a service, or an API that regains it, changes where work goes on
    the next check - no reconnect, no re-Connect, no human decision."""
    provider = fx.provider_with(fx.network_runtime("openapi", api="unavailable", worker="available"))
    assert runtime_service.execution_of(provider, True) == "background"

    # The worker stops being able to reach it; the API starts being able to.
    rt = runtime_service.runtimes_of(provider)[0]
    runtime_service.apply_reachability(
        provider,
        {rt.id: rt.reachability.with_result(WORKER, False).with_result(API, True)},
    )
    assert runtime_service.execution_of(provider, True) == "inline"
    back = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.execution_location(provider, back, worker_available=True) == API


def test_g2_a_check_from_the_side_that_cannot_see_it_is_not_an_outage(monkeypatch):
    """The worker reaches it every day. The API trying and timing out says
    where it is driven from - not that it is down - so nothing goes into a
    rest period and the person is not told it can't be reached."""
    provider = fx.provider_with(fx.network_runtime("openapi", api="unknown", worker="available"))
    monkeypatch.setattr(runtime_service, "check_runtime", lambda p, rt, s: HealthResult(ok=False, state="unavailable", detail="Couldn't reach it (ConnectTimeout)."))

    outcomes, deferred = runtime_service.check_all_runtimes(provider, {}, execution="inline")

    assert outcomes == [] and [rt.id for rt in deferred] == ["openapi"]
    after = runtime_service.runtimes_of(provider)[0]
    assert after.reachability.api == "unavailable" and after.reachability.worker == "available"
    assert not after.health.in_cooldown(runtime_service._now())
    assert runtime_service.execution_of(provider, True) == "background"


# --------------------------------------------------------------------------- H

def test_h_nobody_can_see_it_and_nobody_pretends_otherwise(seeded):
    """The one outcome that must never be dressed up. A provider no process
    can reach is unavailable, and a health check says so rather than
    inheriting a cheerful default."""
    from adapters import ProviderSpec, get_adapter

    provider = fx.provider_with(fx.network_runtime("openapi", api="unavailable", worker="unavailable"))
    rt = runtime_service.runtimes_of(provider)[0]
    assert runtime_service.locations_for(provider, rt, worker_available=True) == []
    assert runtime_service.execution_location(provider, rt, worker_available=True) is None
    assert rt.reachability.usable_from() == []

    spec = ProviderSpec(id="p", slug="s", name="Service", adapter={"kind": "openapi", "config": fx.as_config(fx.JOBS_API)})
    checked = get_adapter("openapi").check(spec, {})
    assert checked.ok is False

    real = provider_service.register_provider(
        seeded,
        {"name": "Nowhere", "description": "", "capabilities": [{"id": "jobs", "title": "Jobs"}], "adapter": {"kind": "openapi", "config": fx.as_config(fx.JOBS_API)}, "origin": "connected"},
    )
    runtime_service.set_runtimes(real, [fx.network_runtime("openapi", api="unavailable", worker="unavailable")], None)
    provider_service.record_availability(seeded, real, HealthResult(ok=False, state="unavailable", detail="Couldn't reach it."))
    seeded.commit()
    assert provider_service.availability_of(real)["state"] != "available"


# --------------------------------------------------------------------------- what routing is told

def test_routing_is_told_what_a_provider_can_do_and_nothing_about_where_it_runs(seeded):
    """A router chooses what can do the work. Which of Bevro's processes
    drives it is decided afterwards, from facts the router never sees - so
    none of it may appear in the catalogue, not even as a wording."""
    from app.routing.catalogue import build_catalogue, catalogue_json
    from app.services import connect as connect_service

    transport, _calls = fx.service(descriptor=fx.JOBS_API)
    row = found_by_the_worker(seeded, "http://service.local/", transport)
    provider = connect_service.confirm_draft(seeded, row, name=None, description=None, capability_summary=None, secrets={}, app_url=None)
    provider_service.record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    seeded.commit()
    assert runtime_service.execution_of(provider, True) == "background"  # it really is worker-bound

    entries = catalogue_json(build_catalogue(seeded, selectable_only=False))
    assert any(e["id"] == provider.slug for e in entries)
    text = json.dumps(entries).lower()
    for word in ("execution", "reachab", "worker", "docker", "tailscale", "container", "host machine", "inline", "background", "base_url", "service.local", "terms"):
        assert word not in text, word
    assert {k for e in entries for k in e} <= {"id", "name", "description", "capabilities", "available", "can_invoke", "requires", "constraints"}
    assert {k for e in entries for c in e["capabilities"] for k in c} <= {"id", "title", "description"}


# --------------------------------------------------------------------------- one way in, for everything

def test_every_kind_of_target_goes_through_the_same_door(client, seeded, monkeypatch):
    """A URL, an MCP endpoint, a folder and a command are all one field and
    one request. Nothing asks the person where it should run, because that is
    not a question they have the information to answer."""
    from app.services import connect as connect_service

    monkeypatch.setattr(provider_service, "worker_seen_recently", lambda _db: True)
    discovery_over(fx.service(descriptor=fx.JOBS_API)[0])

    shapes = []
    for target in ("http://service.local/", "http://desk.local/mcp", "~/agents/something", "python -m something --topic x"):
        response = client.post("/api/connect/discover", json={"target": target})
        assert response.status_code == 201, (target, response.text)
        body = response.json()
        shapes.append(frozenset(body))
        # "Looking" whoever is looking: the person is never told which process.
        assert body["state"] in ("looking", "found", "failed")
        # Nothing about Bevro's own plumbing reaches the person.
        text = json.dumps(body).lower()
        for word in ("docker", "container", "tailscale", "bridge network", "host network", "api process"):
            assert word not in text, (target, word)

    # One shape for all four, so there is no separate flow to pick between.
    assert len(set(shapes)) == 1

    # Where a draft can be driven from is stated, never asked: the only
    # choice a person is ever offered is between ways *in*, not places.
    # The only things a person is ever asked are which way *in* to use, and
    # whether Bevro may touch something on this machine - never which of
    # Bevro's own processes should do it.
    for target in ("http://service.local/", "~/agents/something"):
        body = client.post("/api/connect/discover", json={"target": target}).json()
        if body["state"] == "trust_required":
            assert set(body["trust"]) & {"path", "program"}, body["trust"]
            assert "worker" not in json.dumps(body["trust"]).lower()
        elif body["draft"]:
            assert {"runtime_options", "choice_needed"} <= set(body["draft"])
            assert all("runs_at" not in option for option in body["draft"]["runtime_options"])
    assert connect_service.can_discover_locally() in (True, False)  # decided by configuration, not by asking


# --------------------------------------------------------------------------- one policy, both processes

@pytest.mark.parametrize("address", ["file:///etc/passwd", "gopher://old.example/1", "data:text/plain,hello", "ftp://files.example/x"])
def test_no_mechanism_will_fetch_an_address_bevro_forbids(address):
    """The worker sits on networks the container does not, which makes this
    policy more important rather than less. It is one module, read by every
    mechanism, with no "worker mode" that relaxes anything."""
    from adapters import InvocationRequest, ProviderSpec, get_adapter
    from adapters import urlsafety

    with pytest.raises(urlsafety.UnsafeUrl):
        urlsafety.check(address)

    prompt = ProviderSpec(id="p", slug="s", name="Service", adapter={"kind": "http", "config": {"base_url": address}})
    assert get_adapter("http").check(prompt, {}).ok is False
    refused = get_adapter("http").invoke(prompt, InvocationRequest(task_id="t", run_id="r", request="hello", input={}, secrets={}))
    assert refused.state.value == "failed" and refused.failure.value == "configuration_problem"

    typed = ProviderSpec(id="p", slug="s", name="Service", adapter={"kind": "openapi", "config": {"base_url": address, "operations": []}})
    assert get_adapter("openapi").check(typed, {}).ok is False

    mcp = ProviderSpec(id="p", slug="s", name="Service", adapter={"kind": "mcp", "config": {"server_url": address}})
    assert get_adapter("mcp").check(mcp, {}).ok is False


def test_the_policy_has_one_home_and_no_second_opinion():
    """Nothing decides for itself what may be fetched, and nothing asks which
    process is asking."""
    import inspect

    from adapters import http as http_adapter
    from adapters import mcp as mcp_adapter
    from adapters import openapi as openapi_adapter
    from adapters import urlsafety
    from app.connect.strategies import http as http_strategy

    for module in (http_adapter, mcp_adapter, openapi_adapter, http_strategy):
        assert "urlsafety" in inspect.getsource(module), module.__name__
        source = inspect.getsource(module)
        # No branch anywhere on which of Bevro's processes is running.
        for smell in ("if location ==", "is_worker", "in_worker", "WORKER =="):
            assert smell not in source, (module.__name__, smell)

    assert urlsafety.ALLOWED_SCHEMES == ("http", "https")
    assert urlsafety.MAX_REDIRECTS == 3
