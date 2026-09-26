"""Failed runs explain themselves in plain words, credentials can be added, tasks can be retried."""

import uuid

from adapters import FailureKind, HealthResult, InvocationRequest, InvocationResult, ResultState
from app.config import get_settings
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services.providers import record_availability
from tests.connect_fixtures import make_python_project


def connect_fixture(client, seeded, local_roots, *, with_key: bool) -> dict:
    project = make_python_project(local_roots)
    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    secrets = {"OPENAI_API_KEY": "sk-fixture"} if with_key else {}
    provider = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={"secrets": secrets}).json()
    row = provider_service.get_provider(seeded, uuid.UUID(provider["id"]))
    record_availability(seeded, row, HealthResult(ok=True, state="available"))
    return provider


def run_like_the_worker(seeded, task, secrets: dict[str, str]):
    run = task.runs[-1]
    request = task_service.build_request(seeded, run, secret_store=None).model_copy(update={"secrets": secrets})
    result = task_service.invoke_adapter(run.provider, request)
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result)
    return result


def test_missing_credential_is_explained_and_fixed_from_the_browser(client, seeded, tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        provider = connect_fixture(client, seeded, root, with_key=False)
        # "Missing" with nothing else found; where something *was* found, the
        # note says what, so the two are never the same answer.
        assert provider["credentials"] == [{"name": "OPENAI_API_KEY", "label": "OpenAI credential", "present": False, "source": "missing", "status": "Missing", "note": None, "why": None}]

        # Routed, the missing credential is said before anything starts.
        r = client.post("/api/tasks", json={"request": "Fixture research: what changed among competitors?"})
        assert r.status_code == 503 and r.json()["detail"]["answer"]["outcome"] == "blocked"
        assert r.json()["detail"]["message"] == "Fixture Research Agent can do this, but direct use in Bevro needs a credential."
        # Asked for by name, the run itself explains what is missing.
        r = client.post("/api/tasks", json={"request": "Fixture research: what changed among competitors?", "provider_id": provider["id"]})
        assert r.status_code == 201, r.text
        task = task_service.get_task(seeded, uuid.UUID(r.json()["id"]))
        result = run_like_the_worker(seeded, task, {})
        assert result.failure == FailureKind.CREDENTIAL_REQUIRED

        detail = client.get(f"/api/tasks/{task.id}").json()
        failure = detail["runs"][-1]["failure"]
        assert failure["category"] == "credential_required" and failure["title"] == "Needs a credential"
        assert failure["message"] == "Fixture Research Agent needs an OpenAI credential before it can run."
        assert [a["kind"] for a in failure["actions"]] == ["add_credential", "retry"]
        assert failure["actions"][0]["secret_name"] == "OPENAI_API_KEY"
        text = r.text + client.get(f"/api/tasks/{task.id}").text
        assert "Traceback" not in text and str(root) not in text and "argv" not in text and "exit_code" not in text

        # Add the credential from Manage: stored encrypted, never echoed back.
        r = client.put(f"/api/providers/{provider['id']}/secrets/OPENAI_API_KEY", json={"value": "sk-added-in-browser"})
        assert r.status_code == 200, r.text
        assert r.json()["credentials"] == [{"name": "OPENAI_API_KEY", "label": "OpenAI credential", "present": True, "source": "bevro", "status": "Added", "note": None, "why": None}]
        assert "sk-added-in-browser" not in r.text and "sk-added-in-browser" not in client.get("/api/providers").text
        assert client.put(f"/api/providers/{provider['id']}/secrets/bad name", json={"value": "x"}).status_code == 422

        # Retry the same task: same request, a second run, still one task under Recent.
        r = client.post(f"/api/tasks/{task.id}/retry")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "queued" and len(r.json()["runs"]) == 2 and r.json()["summary"] is None
        seeded.refresh(task)
        result = run_like_the_worker(seeded, task, {"OPENAI_API_KEY": "sk-added-in-browser"})
        assert result.state == ResultState.COMPLETED
        detail = client.get(f"/api/tasks/{task.id}").json()
        assert detail["state"] == "completed" and detail["runs"][-1]["failure"] is None
        assert detail["runs"][0]["failure"]["category"] == "credential_required"  # the first attempt stays on the record
        assert [t["id"] for t in client.get("/api/tasks").json()].count(str(task.id)) == 1
        assert client.post(f"/api/tasks/{task.id}/retry").status_code == 409  # only failed tasks can be retried
    finally:
        get_settings.cache_clear()


def test_failure_categories_map_to_actions(client, seeded, monkeypatch):
    research = provider_service.get_by_slug(seeded, "research")
    cases = {
        FailureKind.PROVIDER_UNAVAILABLE: ("Couldn't be reached", "Research isn't available right now.", ["test_connection", "retry"]),
        FailureKind.INVOCATION_FAILED: ("Didn't finish", "Research started but couldn't finish this task.", ["retry", "manage"]),
        FailureKind.CONFIGURATION_PROBLEM: ("Needs setting up again", "Bevro couldn't start Research with its current connection.", ["manage"]),
        FailureKind.TIMED_OUT: ("Took too long", "Research took too long and was stopped.", ["retry", "manage"]),
        FailureKind.OUTPUT_INVALID: ("Unreadable answer", "Research answered, but Bevro couldn't read the result.", ["retry", "manage"]),
        FailureKind.EXECUTION_FAILED: ("Didn't finish", "Research started but couldn't finish this task.", ["retry", "manage"]),  # the old name still reads
        None: ("Didn't finish", "Research started but couldn't finish this task.", ["retry", "manage"]),
    }
    for kind, (title, message, actions) in cases.items():
        monkeypatch.setattr(task_service, "execute", lambda *a, kind=kind, **k: (InvocationResult(state=ResultState.FAILED, error="technical words", failure=kind), []))
        task_id = client.post("/api/tasks", json={"request": "Find three options", "provider_id": str(research.id)}).json()["id"]
        failure = client.get(f"/api/tasks/{task_id}").json()["runs"][-1]["failure"]
        assert (failure["title"], failure["message"], [a["kind"] for a in failure["actions"]]) == (title, message, actions), kind
        assert "technical words" not in str(failure)


def test_retry_needs_an_available_provider(client, seeded, monkeypatch):
    research = provider_service.get_by_slug(seeded, "research")
    monkeypatch.setattr(task_service, "execute", lambda *a, **k: (InvocationResult(state=ResultState.FAILED, error="x", failure=FailureKind.INVOCATION_FAILED), []))
    task_id = client.post("/api/tasks", json={"request": "Find three options", "provider_id": str(research.id)}).json()["id"]
    research.enabled = False
    seeded.flush()
    assert client.post(f"/api/tasks/{task_id}/retry").status_code == 503
    assert client.post(f"/api/tasks/{uuid.uuid4()}/retry").status_code == 404
