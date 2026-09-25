"""Connect end to end: type a target, confirm the draft, the provider is routable."""

import json
import uuid

import httpx
import pytest

from adapters import HealthResult
from app.config import get_settings
from app.connect import assist
from app.connect.draft import ProviderDraft
from app.connect.service import ConnectionDiscoveryService, set_discovery_service
from app.connect.strategies.command import CommandStrategy
from app.connect.strategies.http import HttpDiscoveryStrategy
from app.connect.strategies.local import LocalProjectStrategy
from app.routing.catalogue import build_catalogue
from app.routing.deterministic import DeterministicRouter
from app.services import connect as connect_service
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services.providers import record_availability
from tests.connect_fixtures import make_python_project, snapshot
from tests.test_connect_http import OPENAPI, fake_service


@pytest.fixture(autouse=True)
def reset_discovery():
    yield
    set_discovery_service(None)


@pytest.fixture
def local_roots(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.setenv("BEVRO_INTEGRATIONS_DIR", str(tmp_path / "integrations"))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def use_fake_http(monkeypatch=None, **kwargs):
    """Discovery and, when a monkeypatch is given, the HTTP adapter both talk to the fake service."""
    transport, calls = fake_service(**kwargs)
    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(transport), LocalProjectStrategy(), CommandStrategy()], use_assist=False))
    if monkeypatch is not None:
        def via_fake(method, url, **kw):
            with httpx.Client(transport=transport) as client:
                return client.request(method, url, **{k: v for k, v in kw.items() if k in ("json", "headers", "params")})

        monkeypatch.setattr(httpx, "request", via_fake)
        monkeypatch.setattr(httpx, "get", lambda url, **kw: via_fake("GET", url, **kw))
        monkeypatch.setattr(httpx, "post", lambda url, **kw: via_fake("POST", url, **kw))
    return calls


# ----------------------------------------------------------------------------- url

def test_url_discovery_confirmation_and_routing(client, seeded, monkeypatch):
    use_fake_http(monkeypatch, openapi=OPENAPI)
    r = client.post("/api/connect/discover", json={"target": "http://sales.local"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "found" and body["target_label"] == "http://sales.local"
    draft = body["draft"]
    assert draft["name"] == "Sales Desk" and draft["confidence_label"] == "Confident"
    assert [c["title"] for c in draft["capabilities"]] == ["Quotes", "Customers"]
    assert "adapter" not in draft and "base_url" not in r.text and "invoke" not in json.dumps(draft)

    r = client.post(f"/api/connect/drafts/{body['id']}/test", json={})
    assert r.status_code == 200 and r.json()["test"]["ok"] is True

    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
    assert r.status_code == 201, r.text
    provider = r.json()
    assert provider["origin"] == "connected" and provider["connection"] == "api" and provider["actions"] == ["ask"]
    assert provider["availability"]["state"] == "available"
    assert "sales.local" not in r.text

    # Immediately in the router catalogue and selectable, with no restart.
    assert "sales-desk" in {e.id for e in build_catalogue(seeded)}
    decision = DeterministicRouter().route(seeded, "Get a quote from the sales desk for 40 chairs")
    assert decision.provider_id == "sales-desk"

    # And a task typed on Home goes there and comes back with the service's answer.
    r = client.post("/api/tasks", json={"request": "Get a quote from the sales desk for 40 chairs"})
    assert r.status_code == 201, r.text
    task = client.get(f"/api/tasks/{r.json()['id']}").json()
    assert task["state"] == "completed" and task["summary"] == "Quote for: Get a quote from the sales desk for 40 chairs"
    assert [a["type"] for a in task["artifacts"]] == ["report"]  # the fixture schema declares only "answer"

    # The draft is used up.
    assert client.post(f"/api/connect/drafts/{body['id']}/confirm", json={}).status_code == 409
    assert client.get(f"/api/connect/drafts/{body['id']}").json()["state"] == "connected"


def test_authentication_is_asked_for_only_when_needed_and_never_returned(client, seeded):
    use_fake_http(openapi=OPENAPI, secured=True)
    body = client.post("/api/connect/discover", json={"target": "http://sales.local"}).json()
    assert body["draft"]["auth"] == {"required": True, "secret_name": "api_key", "label": "API token", "hint": "Sent as a bearer token. Stored encrypted; never shown again.", "why": None}
    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={"secrets": {"api_key": "tok-super-secret"}})
    assert r.status_code == 201 and r.json()["secret_names"] == ["api_key"]
    assert "tok-super-secret" not in r.text and "tok-super-secret" not in client.get("/api/providers").text

    use_fake_http(openapi=OPENAPI)
    body = client.post("/api/connect/discover", json={"target": "http://open.local"}).json()
    assert body["draft"]["auth"]["required"] is False


def test_what_it_is_for_is_kept_even_when_it_cannot_be_connected_yet(client, seeded):
    use_fake_http()
    body = client.post("/api/connect/discover", json={"target": "http://bare.local"}).json()
    assert body["draft"]["needs_description"] is True and body["draft"]["note"]
    assert body["draft"]["description"] == ""  # not "Connected service": it isn't
    r = client.post(f"/api/connect/drafts/{body['id']}/describe", json={"name": "Bare Thing", "capability_summary": "Research, Product strategy, weather forecasts"})
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert [c["id"] for c in draft["capabilities"]] == ["research", "product_strategy", "weather_forecasts"]
    assert draft["name"] == "Bare Thing" and draft["needs_description"] is False and draft["note"] is None
    # Kept on the server: asking again gives the same answer.
    assert client.get(f"/api/connect/drafts/{body['id']}").json()["draft"]["name"] == "Bare Thing"
    # It still can't take work, and Connect says so rather than pretending.
    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
    assert r.status_code == 409 and "send it work" in r.json()["detail"]


def test_an_empty_description_is_refused_plainly(client, seeded):
    use_fake_http()
    body = client.post("/api/connect/discover", json={"target": "http://bare.local"}).json()
    r = client.post(f"/api/connect/drafts/{body['id']}/describe", json={"capability_summary": " , ,"})
    assert r.status_code == 422 and "few words" in r.json()["detail"]


def test_unreachable_and_nonsense_targets_fail_plainly(client, seeded):
    use_fake_http()
    r = client.post("/api/connect/discover", json={"target": "python -m x; rm -rf /"})
    assert r.status_code == 422 and "pipes or redirections" in r.json()["detail"]
    r = client.post("/api/connect/discover", json={"target": "  "})
    assert r.status_code == 422
    assert client.get(f"/api/connect/drafts/{uuid.uuid4()}").status_code == 404


# ----------------------------------------------------------------------------- local

def test_local_project_needs_the_worker_when_the_api_has_no_roots(client, seeded, monkeypatch):
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", "")
    get_settings.cache_clear()
    try:
        r = client.post("/api/connect/discover", json={"target": "~/agents/thing"})
        assert r.status_code == 201
        assert r.json()["state"] == "failed" and "worker" in r.json()["error"]
        # With a live worker it is queued for the host instead.
        claude = provider_service.get_by_slug(seeded, "claude-code")
        record_availability(seeded, claude, HealthResult(ok=False, state="not_installed"))
        r = client.post("/api/connect/discover", json={"target": "~/agents/thing"})
        assert r.json()["state"] == "looking" and r.json()["target_label"] == "thing"
        assert "/agents/" not in r.text
    finally:
        get_settings.cache_clear()


def test_local_project_connects_and_is_routed_and_run(client, seeded, local_roots, tmp_path):
    project = make_python_project(local_roots)
    before = snapshot(project)
    use_fake_http()

    r = client.post("/api/connect/discover", json={"target": str(project)})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "found" and body["target_label"] == "fixture-research"
    draft = body["draft"]
    assert draft["mechanism_label"] == "Runs from this project" and draft["runs_via"] == "Runs from this project" and draft["availability"] == "needs_worker"
    assert draft["runtime"]["credentials"]["label"] == "Missing" and "adapter" not in draft["runtime"] and "kind" not in draft["runtime"]
    assert draft["auth"]["required"] and draft["auth"]["secret_name"] == "OPENAI_API_KEY"
    assert str(tmp_path) not in r.text  # no absolute path reaches the browser
    assert snapshot(project) == before

    # What was checked, one fact each - and not "passed" while the credential is missing.
    r = client.post(f"/api/connect/drafts/{body['id']}/test", json={})
    test = r.json()["test"]
    assert test["ok"] is False and test["next"] == "add_credential", r.text
    assert [(c["label"], c["ok"]) for c in test["checks"]] == [
        ("Found it on this machine", True),
        ("Its Python environment is available", True),
        ("Found the command Bevro will use", True),
        ("No OpenAI credential yet for tasks Bevro starts", False),
        ("It doesn't offer a self-check, so no real task was run", None),
    ]
    r = client.post(f"/api/connect/drafts/{body['id']}/test", json={"secrets": {"OPENAI_API_KEY": "sk-fixture"}})
    assert r.json()["test"]["ok"] is True and "sk-fixture" not in r.text

    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={"secrets": {"OPENAI_API_KEY": "sk-fixture"}})
    assert r.status_code == 201, r.text
    provider = r.json()
    assert provider["connection"] == "command" and provider["secret_names"] == ["OPENAI_API_KEY"]
    assert provider["availability"]["state"] == "unavailable"  # until the worker says it can run it
    assert str(tmp_path) not in r.text

    row = provider_service.get_provider(seeded, uuid.UUID(provider["id"]))
    assert row.adapter["config"]["cwd"] == str(project)  # server-side only
    assert "fixture-research" not in {e.id for e in build_catalogue(seeded)}
    record_availability(seeded, row, HealthResult(ok=True, state="available"))
    assert "fixture-research-agent" in {e.id for e in build_catalogue(seeded)}

    decision = DeterministicRouter().route(seeded, "Fixture research: what changed among competitors this month?")
    assert decision.provider_id == "fixture-research-agent"

    task = task_service.submit(seeded, "Fixture research: what changed among competitors this month?")
    seeded.commit()
    run = task.runs[0]
    assert run.execution == "background" and run.state == "pending"
    # What the worker does on the host, done here in-process.
    request = task_service.build_request(seeded, run, secret_store=None)
    request = request.model_copy(update={"secrets": {"OPENAI_API_KEY": "sk-fixture"}})
    result = task_service.invoke_adapter(row, request)
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result)
    assert task.state == "completed" and task.summary == "Report written to reports/latest-findings.md."
    titles = [a.title for a in task.artifacts]
    assert titles == ["Output from Fixture Research Agent", "latest-findings.md"]
    assert snapshot(project) != before  # the agent wrote its own report, as it always does
    changed = {k for k in snapshot(project) if before.get(k) != snapshot(project)[k]}
    assert changed == {"reports/latest-findings.md"}


def test_worker_handles_pending_drafts_on_the_host(seeded, local_roots, monkeypatch):
    project = make_python_project(local_roots)
    monkeypatch.setattr(connect_service, "can_discover_locally", lambda: False)  # the API in Docker sees no roots
    claude = provider_service.get_by_slug(seeded, "claude-code")
    record_availability(seeded, claude, HealthResult(ok=False, state="not_installed"))
    row = connect_service.start_discovery(seeded, str(project))
    assert row.state == "pending"
    handled = connect_service.run_pending(seeded, [local_roots])
    assert handled == 1
    seeded.refresh(row)
    assert row.state == "found" and row.draft["name"] == "Fixture Research Agent"
    row.state = "testing"
    seeded.commit()
    assert connect_service.run_pending(seeded, [local_roots]) == 1
    seeded.refresh(row)
    # The worker checked everything but the credential, which nobody has given it.
    assert row.state == "found" and row.test["next"] == "add_credential"
    assert all(c["ok"] is not False for c in row.test["checks"] if c.get("kind") != "credential")


def test_paths_outside_the_roots_are_refused_by_the_api(client, seeded, local_roots, tmp_path):
    (tmp_path / "private").mkdir()
    r = client.post("/api/connect/discover", json={"target": str(tmp_path / "private")})
    # Refused, and said in words about this installation rather than about
    # the variable that configured it.
    assert r.json()["state"] == "failed"
    assert "administrator" in r.json()["error"] and "BEVRO_LOCAL_ROOTS" not in r.json()["error"]
    r = client.post("/api/connect/discover", json={"target": str(local_roots / ".." / "private")})
    assert r.json()["state"] == "failed"
    assert str(tmp_path) not in r.text


# ----------------------------------------------------------------------------- command

def test_command_target_is_classified_not_run(client, seeded, local_roots):
    r = client.post("/api/connect/discover", json={"target": "python -m my_agent --topic x"})
    body = r.json()
    assert body["state"] == "found"
    assert body["draft"]["mechanism_label"] == "Runs on this machine" and body["draft"]["invocation_label"] == "python -m my_agent --topic x"
    assert "Takes the request with --topic" in body["draft"]["evidence"]
    assert body["draft"]["needs_description"] is True  # a bare command says nothing about what it is for
    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
    assert r.status_code == 422 and "use it for" in r.json()["detail"]
    r = client.post(f"/api/connect/drafts/{body['id']}/describe", json={"capability_summary": "search documents, create reports"})
    assert r.json()["draft"]["needs_description"] is False
    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
    assert r.status_code == 201 and r.json()["connection"] == "command"
    assert [c["id"] for c in r.json()["capabilities"]] == ["search_documents", "create_reports"]
    row = provider_service.get_provider(seeded, uuid.UUID(r.json()["id"]))
    assert row.adapter["config"]["argv"] == ["python", "-m", "my_agent"] and row.adapter["config"]["input"] == {"mode": "flag", "flag": "--topic"}
    assert "cwd" not in row.adapter["config"]  # runs in Bevro's integration folder, not in any project


# ----------------------------------------------------------------------------- advanced setup

def test_advanced_setup_fallback_covers_api_command_and_mcp(client, seeded):
    r = client.post("/api/providers", json={"name": "Quoter", "method": "api", "capabilities": ["Quotes"], "details": {"base_url": "http://q.local/", "request_template": 'POST /ask {"query": "{request}"}', "response_field": "answer"}, "secrets": {"api_key": "k"}})
    assert r.status_code == 201, r.text
    row = provider_service.get_provider(seeded, uuid.UUID(r.json()["id"]))
    assert row.adapter["config"] == {"base_url": "http://q.local", "invoke": {"method": "POST", "path": "/ask", "body": {"query": "{request}"}}, "response": {"text": "answer", "summary": "answer"}, "auth": {"type": "bearer", "secret": "api_key"}}

    r = client.post("/api/providers", json={"name": "Runner", "method": "command", "details": {"command": "python -m runner", "working_directory": "/srv/agents/runner", "input_flag": "--task", "secret_env": "RUNNER_KEY"}, "secrets": {"RUNNER_KEY": "v"}})
    assert r.status_code == 201, r.text
    row = provider_service.get_provider(seeded, uuid.UUID(r.json()["id"]))
    assert row.adapter["config"]["argv"] == ["python", "-m", "runner"] and row.adapter["config"]["cwd"] == "/srv/agents/runner"
    assert row.adapter["config"]["input"] == {"mode": "flag", "flag": "--task"} and row.adapter["config"]["secret_env"] == ["RUNNER_KEY"]
    assert "/srv/agents" not in r.text

    r = client.post("/api/providers", json={"name": "Tools", "method": "mcp", "details": {"command": "npx some-mcp", "tool": "search:query"}})
    assert r.status_code == 201, r.text
    row = provider_service.get_provider(seeded, uuid.UUID(r.json()["id"]))
    assert row.adapter["config"]["argv"] == ["npx", "some-mcp"] and row.adapter["config"]["tool"] == {"name": "search", "argument": "query"}

    assert client.post("/api/providers", json={"name": "X", "method": "command", "details": {"command": "python -m x | cat"}}).status_code == 422
    assert client.post("/api/providers", json={"name": "X", "method": "api", "details": {"base_url": "http://x", "request_template": "nonsense"}}).status_code == 422


# ----------------------------------------------------------------------------- assist

def test_assist_is_off_in_deterministic_mode_and_sees_no_env_contents(monkeypatch, local_roots):
    project = make_python_project(local_roots, env_secret="LEAKY-VALUE-123")
    from app.connect.strategies.base import DiscoveryContext
    from app.connect.targets import classify_target

    calls: list[str] = []

    class FakeModel:
        name = "fake"

        def structured(self, system, user, schema, name, max_tokens=400):
            calls.append(user)
            return {"name": "Fixture Research", "description": "Finds competitor moves and suggests strategy.", "capabilities": [{"id": "research", "title": "Research"}, {"id": "competitor_analysis", "title": "Competitor analysis"}]}

    monkeypatch.setattr("app.routing.models.get_routing_model", lambda settings=None: FakeModel())
    monkeypatch.setenv("BEVRO_ROUTER_MODE", "deterministic")
    get_settings.cache_clear()
    draft = ConnectionDiscoveryService().discover(classify_target(str(project)), DiscoveryContext(roots=[local_roots]))
    assert calls == [] and not draft.assisted  # deterministic mode: the model is never called

    monkeypatch.setenv("BEVRO_ROUTER_MODE", "llm")
    get_settings.cache_clear()
    draft = ConnectionDiscoveryService().discover(classify_target(str(project)), DiscoveryContext(roots=[local_roots]))
    get_settings.cache_clear()
    assert draft.assisted and draft.description == "Finds competitor moves and suggests strategy."
    assert [c.id for c in draft.capabilities] == ["research", "competitor_analysis"]
    sent = calls[0]
    assert "LEAKY-VALUE-123" not in sent and str(local_roots) not in sent and ".env" not in sent
    assert "Researches competitor moves" in sent  # the README excerpt is allowed


def test_assist_failure_keeps_the_deterministic_draft(monkeypatch):
    draft = ProviderDraft(name="X", mechanism="http", adapter={"kind": "http", "config": {}}, confidence="medium")

    class Broken:
        name = "broken"

        def structured(self, *a, **k):
            raise RuntimeError("no")

    monkeypatch.setattr(assist, "enabled", lambda: True)
    monkeypatch.setattr("app.routing.models.get_routing_model", lambda settings=None: Broken())
    assert assist.refine(draft) == draft
