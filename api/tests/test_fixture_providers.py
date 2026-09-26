"""Three runtime types, one path: URL / folder / MCP in, ProviderRun and artifacts out.

Fixture A: an already-running HTTP agent (own credential, JSON task endpoint), by URL.
Fixture B: a local CLI Python agent (prompt in, report file out), by folder.
Fixture C: an MCP agent (runtime separate from the repository), by folder.
Plus an unknown folder. No provider-specific code anywhere on the way.
"""

import socket
import uuid

import httpx
import pytest

from adapters import HealthResult
from app.config import get_settings
from app.routing.catalogue import build_catalogue
from app.routing.deterministic import DeterministicRouter
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services.providers import record_availability
from tests import fixture_http_agent
from tests.connect_fixtures import make_mcp_project, make_python_project, snapshot


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def local_roots(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.setenv("BEVRO_INTEGRATIONS_DIR", str(tmp_path / "integrations"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def http_agent(monkeypatch):
    port = free_port()
    monkeypatch.setenv("FIXTURE_UPSTREAM_KEY", "its-own-secret")
    server = fixture_http_agent.serve(port)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def connect(client, target: str) -> dict:
    body = client.post("/api/connect/discover", json={"target": target}).json()
    assert body["state"] == "found", body
    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
    assert r.status_code == 201, r.text
    return r.json()


def worker_run(seeded, task_id: str, secrets: dict[str, str] | None = None) -> dict:
    """What the host worker does for a background run, done in-process."""
    task = task_service.get_task(seeded, uuid.UUID(task_id))
    run = task.runs[-1]
    request = task_service.build_request(seeded, run, secret_store=None).model_copy(update={"secrets": secrets or {}})
    result, artifacts = task_service.execute(run.provider, request)
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result, artifacts)
    seeded.refresh(task)
    return {"state": task.state, "summary": task.summary, "artifacts": [(a.type, a.title) for a in task.artifacts if a.provider_run_id == run.id]}


def test_three_runtime_types_connect_route_and_run_the_same_way(client, seeded, local_roots, http_agent):
    # A. Already-running HTTP agent by URL: nothing to configure, nothing to ask.
    a = connect(client, http_agent)
    assert a["runtime"]["display_name"] == "Connected over the network" and a["runtime"]["credentials_label"] == "Managed by provider"
    assert a["credentials"] == [] and a["availability"]["state"] == "available"

    # B. Local CLI agent by folder: runs from its project through the worker; needs one credential.
    project_b = make_python_project(local_roots)
    before_b = snapshot(project_b)
    b = connect(client, str(project_b))
    assert b["runtime"]["display_name"] == "Runs from this project" and b["runtime"]["credentials_label"] == "Missing"
    assert snapshot(project_b) == before_b

    # C. MCP agent by folder: uses MCP on this machine; no credential.
    project_c = make_mcp_project(local_roots)
    before_c = snapshot(project_c)
    body = client.post("/api/connect/discover", json={"target": str(project_c)}).json()
    assert body["draft"]["runs_via"] == "Uses MCP on this machine"
    test = client.post(f"/api/connect/drafts/{body['id']}/test", json={}).json()  # starts it once, reads its tools
    assert test["test"]["ok"] and [c["id"] for c in test["draft"]["capabilities"]] == ["ask", "delete_all"]
    c = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={}).json()
    assert c["runtime"]["display_name"] == "Uses MCP on this machine" and c["runtime"]["credentials_label"] == "None needed"
    assert snapshot(project_c) == before_c

    # None of the technical detail reaches the browser.
    listing = client.get("/api/providers").text
    for forbidden in (str(local_roots), "argv", "cwd", "server_url", "base_url", "127.0.0.1", "its-own-secret"):
        assert forbidden not in listing, forbidden

    # The worker says B and C can run here; A already can. All three enter the router catalogue.
    for p in (b, c):
        record_availability(seeded, provider_service.get_provider(seeded, uuid.UUID(p["id"])), HealthResult(ok=True, state="available", credentials={"OPENAI_API_KEY": "missing"} if p is b else {}))
    selectable = {e.id for e in build_catalogue(seeded)}
    assert {a["slug"], b["slug"], c["slug"]} <= selectable
    catalogue_text = str([e.model_dump() for e in build_catalogue(seeded)])
    assert "http" not in catalogue_text.lower().replace("http_result", "") or "base_url" not in catalogue_text

    # The router chooses by capability and name, never by mechanism.
    router = DeterministicRouter()
    assert router.route(seeded, "Fixture Desk: draft a reply about widgets").provider_id == a["slug"]
    assert router.route(seeded, "Fixture research on competitor moves this month").provider_id == b["slug"]
    assert router.route(seeded, "Ask Notes MCP to search my notes for lunch").provider_id == c["slug"]

    # A: inline, straight through the same ProviderRun path.
    ta = client.post("/api/tasks", json={"request": "Fixture Desk: draft a reply about widgets"}).json()
    detail = client.get(f"/api/tasks/{ta['id']}").json()
    assert detail["state"] == "completed" and detail["summary"].startswith("Widget answer for:")
    assert [x["type"] for x in detail["artifacts"]] == ["report", "deep_link"]

    # B: background. Asked for by what it does, it is the one - and Bevro says
    # up front that direct use needs a credential, rather than starting work
    # that can only fail.
    answer = client.post("/api/tasks", json={"request": "Fixture research on competitor moves this month"}).json()["detail"]["answer"]
    assert answer["outcome"] == "blocked" and answer["item"]["id"] == b["id"]
    # Asked for by name anyway, it fails fast with an actionable category, then works once given one.
    tb = client.post("/api/tasks", json={"request": "Fixture research on competitor moves this month", "provider_id": b["id"]}).json()
    assert tb["state"] == "queued" and tb["runs"][0]["state"] == "pending"
    outcome = worker_run(seeded, tb["id"])
    assert outcome["state"] == "failed"
    assert client.get(f"/api/tasks/{tb['id']}").json()["runs"][-1]["failure"]["category"] == "credential_required"
    client.put(f"/api/providers/{b['id']}/secrets/OPENAI_API_KEY", json={"value": "sk-fixture"})
    assert client.post(f"/api/tasks/{tb['id']}/retry").status_code == 200
    outcome = worker_run(seeded, tb["id"], {"OPENAI_API_KEY": "sk-fixture"})
    assert outcome["state"] == "completed" and outcome["artifacts"] == [("report", "Output from Fixture Research Agent"), ("report", "latest-findings.md")]

    # C: background MCP through the worker path, same contract.
    tc = client.post("/api/tasks", json={"request": "Ask Notes MCP to search my notes for lunch"}).json()
    outcome = worker_run(seeded, tc["id"])
    assert outcome["state"] == "completed" and outcome["summary"].startswith("You asked:")
    assert outcome["artifacts"] == [("report", "Answer from Notes MCP")]

    # Everything is under Recent, and the details endpoint names kinds only.
    titles = {t["title"] for t in client.get("/api/tasks").json()}
    assert {ta["title"], tb["title"], tc["title"]} <= titles
    details = client.get(f"/api/providers/{c['id']}/details").json()
    assert details["active_runtime"]["kind"] == "mcp_stdio" and details["active_runtime"]["adapter"] == "mcp"
    assert str(local_roots) not in str(details)


def test_unknown_folder_gets_no_runtime_and_advanced_setup(client, seeded, local_roots):
    (local_roots / "photos").mkdir()
    (local_roots / "photos" / "a.txt").write_text("x", encoding="utf-8")
    body = client.post("/api/connect/discover", json={"target": str(local_roots / "photos")}).json()
    assert body["state"] == "found" and body["draft"]["invocable"] is False and body["draft"]["runs_via"] is None
    assert client.post(f"/api/connect/drafts/{body['id']}/confirm", json={}).status_code == 409


def test_reconnect_refreshes_runtimes_from_the_source(client, seeded, local_roots, http_agent):
    a = connect(client, http_agent)
    provider = provider_service.get_provider(seeded, uuid.UUID(a["id"]))
    assert provider.source["kind"] == "url" and provider.source["target"] == http_agent
    assert provider.source["validated_at"]  # when Bevro last proved this source
    provider.runtimes = []
    seeded.flush()
    r = client.post(f"/api/providers/{a['id']}/reconnect")
    assert r.status_code == 200 and r.json()["runtime"]["display_name"] == "Connected over the network"
    seeded.refresh(provider)
    assert provider.active_runtime and provider.runtimes
