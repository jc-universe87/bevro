"""The coding flow through HTTP: the question, the answer, and what is (not) exposed."""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters import HealthResult
from app.models import Workspace
from app.services import providers as provider_service


@pytest.fixture
def workspace(seeded, tmp_path: Path) -> Workspace:
    provider_service.record_availability(seeded, provider_service.get_by_slug(seeded, "claude-code"), HealthResult(ok=True, state="available"))
    ws = Workspace(slug="demo", name="Demo", path=str(tmp_path), description="test", enabled=True, permissions=["read", "write", "run_commands"])
    seeded.add(ws)
    seeded.flush()
    return ws


def test_without_a_worker_claude_is_listed_calmly_and_coding_requests_fail_clearly(client, seeded):
    providers = client.get("/api/providers").json()
    claude = next(p for p in providers if p["slug"] == "claude-code")
    assert claude["actions"] == []
    assert claude["availability"] == {"state": "unavailable", "note": "Not available on this installation"}
    r = client.post("/api/tasks", json={"request": "Fix the bug in the login page"})
    assert r.status_code == 503
    assert r.json()["detail"] == {"message": "Coding help isn't set up on this installation yet.", "reason": "no_provider"}
    r = client.post("/api/tasks", json={"request": "Fix it", "provider_id": claude["id"]})
    assert r.status_code == 503 and "isn't available" in r.json()["detail"]["message"]
    r = client.post("/api/tasks", json={"request": "Allocate participants for the spring conference"})
    assert r.status_code == 201


def test_provider_listing_never_exposes_internals(client, seeded, workspace, tmp_path):
    body = client.get("/api/providers").text
    for forbidden in ("\"adapter\"", "\"config\"", "\"cli\"", "/usr", "/home", str(tmp_path), "\"ref\"", "base_url", "checked_at", "\"detail\""):
        assert forbidden not in body, forbidden


def test_coding_request_asks_which_project_then_queues(client, seeded, workspace, tmp_path):
    r = client.post("/api/tasks", json={"request": "Fix the spacing issue on the Recent page and run the frontend tests"})
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["state"] == "needs_input"
    assert task["summary"] == "Which project should I work on?"
    assert task["input_request"] == {"question": "Which project should I work on?", "kind": "choice", "options": [{"value": str(workspace.id), "label": "Demo"}]}
    assert str(tmp_path) not in r.text  # never the path

    r = client.post(f"/api/tasks/{task['id']}/input", json={"value": "/etc"})
    assert r.status_code == 422
    r = client.post(f"/api/tasks/{task['id']}/input", json={"value": "../../"})
    assert r.status_code == 422

    r = client.post(f"/api/tasks/{task['id']}/input", json={"value": str(workspace.id)})
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["state"] == "queued"  # waits for the worker; nothing ran in the API process
    assert task["input_request"] is None
    run = task["runs"][0]
    assert run["workspace"] == {"id": str(workspace.id), "name": "Demo"}
    assert run["permissions"] == ["Read and modify files in Demo", "Run project commands"]
    assert str(tmp_path) not in r.text
    for forbidden in ("input", "meta", "worker_id", "heartbeat_at", "cancel_requested", "log"):
        assert forbidden not in run


def test_workspace_listing_hides_paths(client, seeded, workspace, tmp_path):
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    [ws] = r.json()
    assert set(ws) == {"id", "name", "description", "permissions"}
    assert str(tmp_path) not in r.text


def test_submit_with_arbitrary_workspace_value_is_rejected(client, seeded, workspace, tmp_path):
    providers = client.get("/api/providers").json()
    claude = next(p for p in providers if p["slug"] == "claude-code")
    r = client.post("/api/tasks", json={"request": "do it", "provider_id": claude["id"], "workspace_id": str(tmp_path)})
    assert r.status_code == 422
    r = client.post("/api/tasks", json={"request": "do it", "provider_id": claude["id"], "workspace_id": str(workspace.id)})
    assert r.status_code == 201 and r.json()["state"] == "queued"


def test_ask_claude_from_agents_screen(client, seeded, workspace):
    providers = client.get("/api/providers").json()
    claude = next(p for p in providers if p["slug"] == "claude-code")
    assert claude["actions"] == ["ask"] and claude["connection"] == "built-in"
    r = client.post("/api/tasks", json={"request": "Add a README", "provider_id": claude["id"]})
    assert r.status_code == 201 and r.json()["state"] == "needs_input"
    # cancelling while waiting for input is fine
    r = client.post(f"/api/tasks/{r.json()['id']}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
