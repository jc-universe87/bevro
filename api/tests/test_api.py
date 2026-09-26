"""End-to-end through the HTTP layer, with provider work run inline."""

from app.services.artifacts import KNOWN_TYPES


def submit(client, **body):
    """POST answers as soon as the task is queued; the work runs afterwards.
    Re-fetch to see the outcome, exactly as the browser does."""
    r = client.post("/api/tasks", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["state"] == "queued"
    return client.get(f"/api/tasks/{r.json()['id']}").json()


def test_home_flow_submit_and_find_under_recent(client, seeded):
    task = submit(client, request="Move my meetings to free up Friday afternoon")
    assert task["state"] == "completed"
    assert task["summary"] == "Done. 3 meetings moved. 1 needs your reply."
    assert task["provider"]["name"] == "Calendar Demo"
    link = next(a for a in task["artifacts"] if a["type"] == "deep_link")
    assert link["title"] == "Open in Calendar" and link["known"] is True

    r = client.get("/api/tasks", params={"q": "friday"})
    assert [t["id"] for t in r.json()] == [task["id"]]

    r = client.get(f"/api/tasks/{task['id']}")
    assert r.status_code == 200 and r.json()["runs"][0]["state"] == "completed"


def test_run_output_never_includes_raw_input_or_logs(client, seeded):
    task = submit(client, request="Find three options")
    run = task["runs"][0]
    assert set(run) == {"id", "provider", "state", "result_summary", "error_summary", "failure", "recovered", "phase", "steps", "tried", "workspace", "permissions", "started_at", "completed_at"}
    assert "input" not in run and "meta" not in run and "worker_id" not in run


def test_artifact_content_is_served(client, seeded):
    task = submit(client, request="Find three options")
    note = task["artifacts"][0]
    assert note["content_url"]
    r = client.get(note["content_url"])
    assert r.status_code == 200
    assert r.text.startswith("# Find three options")


def test_secret_key_is_generated_when_missing(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services.secrets import load_or_create_key

    monkeypatch.setenv("BEVRO_SECRET_KEY", "")
    monkeypatch.setenv("BEVRO_SECRET_KEY_FILE", str(tmp_path / "secret.key"))
    get_settings.cache_clear()
    try:
        first = load_or_create_key()
        assert len(first) == 44 and (tmp_path / "secret.key").is_file()
        assert load_or_create_key() == first  # stable across restarts
    finally:
        get_settings.cache_clear()


def test_providers_hide_adapter_config_and_secrets(client, seeded):
    r = client.post(
        "/api/providers",
        json={
            "name": "Sales Desk",
            "description": "Quotes",
            "capabilities": ["Quote"],
            "method": "api",
            "details": {"base_url": "http://internal-sales:9000"},
            "secrets": {"api_key": "super-secret-value"},
            "app_url": "http://sales.local/app",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "super-secret-value" not in r.text
    assert "adapter" not in body and "config" not in body
    assert body["secret_names"] == ["api_key"]
    assert body["connection"] == "api"
    assert body["actions"] == ["ask", "open"]
    assert body["capabilities"] == [{"id": "quote", "title": "Quote"}]

    listing = client.get("/api/providers").text
    assert "super-secret-value" not in listing and "internal-sales" not in listing


def test_connect_requires_an_address_for_api(client, seeded):
    r = client.post("/api/providers", json={"name": "X", "method": "api", "details": {}})
    assert r.status_code == 422


def test_mcp_provider_answers_honestly(client, seeded):
    p = client.post(
        "/api/providers",
        json={"name": "Notes MCP", "method": "mcp", "details": {"server_url": "http://mcp.local"}},
    ).json()
    task = submit(client, request="Find my notes", provider_id=p["id"])
    assert task["state"] == "failed"
    # No tool was chosen for plain requests (Advanced setup lets you pick one); the answer says so.
    assert "none that takes a plain request" in task["summary"]


def test_create_preview_is_plain_words(client, seeded):
    r = client.post("/api/create/preview", json={"description": "Summarise my inbox each morning and draft replies"})
    assert r.status_code == 200
    preview = r.json()
    assert preview["name"] and preview["description"]
    assert {"Summarise", "Handle email", "Draft text"} & set(preview["can"])
    assert "An account you connect" in preview["needs"]
    assert preview["produces"] and preview["schedule"] == "each morning"
    assert preview["can_build"] is False  # nothing connected can build one in this installation
    # The spec travels back for the build, but the person sees words, not JSON.
    assert preview["spec"]["source"] in ("rules", "model")


def test_create_activate_still_records_a_described_agent(client, seeded):
    r = client.post("/api/create/activate", json={"name": "Inbox Helper", "description": "Summarise my inbox", "capabilities": [{"id": "summarise", "title": "Summarise"}], "permissions": ["Read your inbox"], "source_description": "Summarise my inbox"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["origin"] == "created" and created["connection"] == "declared"
    assert created["actions"] == []


def test_shipped_providers_can_be_removed_like_anything_else(client, seeded):
    """Everything in Apps & agents is the person's. Something Bevro ships is
    no exception, and starting up again does not bring it back."""
    from app.services import providers as provider_service

    providers = client.get("/api/providers").json()
    coding = next(p for p in providers if p["slug"] == "claude-code")
    assert client.delete(f"/api/providers/{coding['id']}").status_code == 204
    provider_service.seed_examples(seeded, demo=False)
    assert "claude-code" not in {p["slug"] for p in client.get("/api/providers").json()}


def test_known_types_cover_the_brief():
    assert {"text", "note", "file", "report", "image", "structured", "interactive", "deep_link", "mini_app"} <= KNOWN_TYPES


def test_meta_reports_routing_mode_without_configuration_details(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["routing"] == {"mode": "deterministic"}
    assert "key" not in r.text.lower() and "base_url" not in r.text


def test_an_unexpected_error_is_still_a_sentence(db, monkeypatch):
    """A bug must not reach the browser as "Internal Server Error"."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.services import tasks as task_service

    def boom(*a, **k):
        raise RuntimeError("something deep broke")

    monkeypatch.setattr(task_service, "list_tasks", boom)
    app.dependency_overrides[get_db] = lambda: db
    # A browser gets the response; only the test client re-raises by default.
    # Deliberately not used as a context manager: that would run the app's
    # startup seeding against a session outside this test's transaction.
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/api/tasks")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail == "Something went wrong at Bevro's end. The details are in the server log."
    assert "RuntimeError" not in response.text and "Traceback" not in response.text
