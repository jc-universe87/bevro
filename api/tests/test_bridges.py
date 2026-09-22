"""Integration bridges: Bevro builds its own way in, and never touches the project.

Everything here uses fixture projects and a stand-in builder; no model is called.
"""

import json
import shutil
import uuid
from pathlib import Path

import pytest

from adapters import HealthResult
from app.config import get_settings
from app.connect.bridge import build_prompt, build_spec, callable_surface, changed_files, fingerprint, needs_bridge, read_manifest, runtime_from_manifest, snapshot
from app.connect.inspect import Project
from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext
from app.connect.strategies.docker import inspect_docker
from app.connect.strategies.local import LocalProjectStrategy
from app.connect.strategies.node import inspect_node
from app.connect.strategies.python import inspect_python
from app.connect.strategies.scripts import inspect_scripts
from app.connect.targets import classify_target
from app.services import bridges as bridge_service
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.services import tasks as task_service
from app.services.providers import record_availability
from tests import fake_builder
from tests.connect_fixtures import make_empty_project, make_import_only_project, make_module_only_node_project, make_python_project


@pytest.fixture
def roots(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.setenv("BEVRO_INTEGRATIONS_DIR", str(tmp_path / "integrations"))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def builder(seeded, monkeypatch):
    """A provider that can build connections, found by capability alone."""
    fake_builder.BEHAVIOUR["mode"] = "python"
    provider = provider_service.register_provider(seeded, dict(fake_builder.MANIFEST))
    seeded.flush()
    yield provider
    fake_builder.BEHAVIOUR["mode"] = "python"


def discover(project: Path, root: Path):
    return ConnectionDiscoveryService([LocalProjectStrategy(probe_host=False)], use_assist=False).discover(classify_target(str(project)), DiscoveryContext(roots=[root]))


def connect_draft(client, project: Path) -> dict:
    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    assert body["state"] == "found", body
    return body


def carry_out(seeded, roots, times: int = 3) -> None:
    """What the worker does on its loop, done here."""
    for _ in range(times):
        bridge_service.run_pending(seeded, [roots])


def run_the_build_task(seeded) -> None:
    """The coding provider's run, as the worker would drive it."""
    from app.domain.run_state import RunState

    for task in task_service.list_tasks(seeded, state="queued"):
        run = task.runs[-1]
        if run.state != RunState.PENDING:
            continue
        request = task_service.build_request(seeded, run, secret_store=None)
        result, artifacts = task_service.execute(run.provider, request)
        task_service.begin_run(seeded, run)
        task_service.finish_run(seeded, run, result, artifacts)


# ----------------------------------------------------------------------------- when a bridge is warranted

def test_a_bridge_is_offered_only_when_there_is_code_and_no_way_in(roots):
    library = discover(make_import_only_project(roots), roots)
    assert not library.invocable and needs_bridge(library) and library.public()["needs_bridge"] is True
    assert library.callable_evidence["modules"][0]["module"] == "widget_brain.analysis"

    # A project that can already take a task is never given a bridge.
    runnable = discover(make_python_project(roots), roots)
    assert runnable.invocable and not needs_bridge(runnable) and runnable.public()["needs_bridge"] is False

    # Nor is a folder with nothing to call.
    nothing = discover(make_empty_project(roots), roots)
    assert not nothing.invocable and not needs_bridge(nothing)


def test_the_specification_is_bounded_and_carries_nothing_private(roots):
    project = make_import_only_project(roots)
    (project / ".env").write_text("SECRET_TOKEN=do-not-leak\n", encoding="utf-8")
    inspected = Project(project)
    findings = [f for f in (inspect_python(inspected), inspect_node(inspected), inspect_docker(inspected), inspect_scripts(inspected)) if f]
    spec = build_spec(provider_id="p1", provider_name="Widget Brain", description="Answers widget questions", project_path=str(project), bridge_dir=str(roots / "out"), project=inspected, findings=findings, capabilities=["research"], readme_excerpt="Answers questions about the widget market.")
    prompt = build_prompt(spec)

    assert spec.language == "python" and spec.evidence["modules"][0]["callables"][0]["name"] == "answer"
    assert "do-not-leak" not in prompt and "SECRET_TOKEN" not in prompt
    assert str(project) in prompt and str(roots / "out") in prompt
    assert "Write only inside" in prompt or "Write the bridge here, and nowhere else" in prompt
    assert "Never read, copy or print credentials" in prompt
    assert len(prompt) < 12_000  # a description, not a repository


def test_the_builder_is_chosen_by_capability_not_by_name(seeded, builder):
    assert [p.slug for p in bridge_service.builder_candidates(seeded)] == ["fixture-builder"]
    assert bridge_service.bridge_possible(seeded)
    builder.capabilities = [{"id": "gardening", "title": "Gardening"}]
    seeded.flush()
    assert not bridge_service.bridge_possible(seeded)
    # Any provider declaring plain coding can build one too.
    builder.capabilities = [{"id": "coding", "title": "Coding"}]
    seeded.flush()
    assert [p.slug for p in bridge_service.builder_candidates(seeded)] == ["fixture-builder"]


def test_without_a_builder_bevro_says_so_instead_of_failing_quietly(client, seeded, roots):
    project = make_import_only_project(roots)
    body = connect_draft(client, project)
    assert body["draft"]["needs_bridge"] is True and body["draft"]["bridge_possible"] is False
    r = client.post(f"/api/connect/drafts/{body['id']}/bridge")
    assert r.status_code == 503 and "no connected agent can build one" in r.json()["detail"]


# ----------------------------------------------------------------------------- building one

def test_a_python_bridge_is_built_validated_and_registered(client, seeded, roots, builder):
    project = make_import_only_project(roots)
    before = snapshot(project)
    body = connect_draft(client, project)
    assert body["draft"]["bridge_possible"] is True

    r = client.post(f"/api/connect/drafts/{body['id']}/bridge")
    assert r.status_code == 201, r.text
    status = r.json()
    assert status["state"] == "preparing" and status["note"] == "Preparing connection…"
    provider_id = uuid.UUID(status["provider_id"])

    # It shows under Agents at once, with plain wording and no way to ask it yet.
    listed = next(p for p in client.get("/api/providers").json() if p["id"] == str(provider_id))
    assert listed["runtime"]["display_name"] == "Preparing connection…" and listed["actions"] == []

    # The worker prepares the work and hands it to the builder.
    carry_out(seeded, roots, 1)
    provider = provider_service.get_provider(seeded, provider_id)
    assert bridge_service.build_state(provider) == "building"
    task = bridge_service.build_task(seeded, provider)
    assert task is not None and task.title == f"Preparing a connection for {provider.name}"
    assert client.get(f"/api/connect/bridges/{provider_id}").json()["steps"] == ["Inspecting project", "Building connection"]

    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)

    seeded.refresh(provider)
    assert bridge_service.build_state(provider) is None
    runtime = bridge_service.bridge_runtime(provider)
    assert runtime is not None and runtime.kind == "cli" and runtime.display_name == "Runs through a connection Bevro built"
    assert runtime.adapter["config"]["input"] == {"mode": "stdin", "format": "json"} and runtime.adapter["config"]["output"] == {"modes": ["json"]}

    # The project is exactly as it was; everything generated is Bevro's own.
    assert snapshot(project) == before
    folder = bridge_service.integration_dir(provider_id)
    assert {p.name for p in folder.iterdir()} >= {"bridge.py", "bridge.json", "README.md"}
    manifest = read_manifest(folder)
    assert manifest["provider_id"] == str(provider_id) and manifest["validation"] == "passed"
    assert manifest["built_by"] == "fixture-builder" and manifest["source"]["fingerprint"] == fingerprint(project)
    assert "secret" not in json.dumps(manifest).lower()

    # And it is now an ordinary provider: available, askable, and it answers.
    record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    listed = next(p for p in client.get("/api/providers").json() if p["id"] == str(provider_id))
    assert listed["actions"] == ["ask"] and listed["runtime"]["display_name"] == "Runs through a connection Bevro built"
    assert str(roots) not in str(listed)

    task = task_service.submit(seeded, "What changed in widgets?", provider=provider)
    seeded.commit()
    run = task.runs[-1]
    request = task_service.build_request(seeded, run, secret_store=None)
    result, artifacts = task_service.execute(run.provider, request)
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result, artifacts)
    assert task.state == "completed" and task.summary == "Widget analysis for: What changed in widgets?"
    assert [(a.type, a.title) for a in task.artifacts] == [("report", "Answer")]
    assert snapshot(project) == before


def test_a_node_bridge_is_built_the_same_way(client, seeded, roots, builder):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    fake_builder.BEHAVIOUR["mode"] = "node"
    project = make_module_only_node_project(roots)
    before = snapshot(project)
    body = connect_draft(client, project)
    assert body["draft"]["needs_bridge"] is True
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)
    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)

    provider = provider_service.get_provider(seeded, provider_id)
    runtime = bridge_service.bridge_runtime(provider)
    assert runtime is not None and runtime.adapter["config"]["argv"] == ["node", "bridge.js"]
    assert snapshot(project) == before

    record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    task = task_service.submit(seeded, "Summarise my notes", provider=provider)
    seeded.commit()
    run = task.runs[-1]
    result, artifacts = task_service.execute(run.provider, task_service.build_request(seeded, run, secret_store=None))
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result, artifacts)
    assert task.state == "completed" and task.summary == "Summary of: Summarise my notes"


# ----------------------------------------------------------------------------- when it goes wrong

def test_a_bridge_that_does_not_answer_properly_is_not_registered(client, seeded, roots, builder):
    fake_builder.BEHAVIOUR["mode"] = "broken"
    project = make_import_only_project(roots)
    body = connect_draft(client, project)
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)
    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)

    provider = provider_service.get_provider(seeded, provider_id)
    assert bridge_service.bridge_runtime(provider) is None
    status = client.get(f"/api/connect/bridges/{provider_id}").json()
    assert status["state"] == "failed" and status["note"] == "Bevro couldn't create a reliable connection for this project."
    listed = next(p for p in client.get("/api/providers").json() if p["id"] == str(provider_id))
    assert listed["actions"] == []  # nothing broken is left active
    assert (bridge_service.integration_dir(provider_id) / "validation-failed.json").is_file()


def test_a_builder_that_touches_the_project_is_refused(client, seeded, roots, builder):
    fake_builder.BEHAVIOUR["mode"] = "touch_source"
    project = make_import_only_project(roots)
    # The person already had uncommitted work in there; it must survive untouched.
    (project / "notes.md").write_text("my own notes\n", encoding="utf-8")
    before = snapshot(project)

    body = connect_draft(client, project)
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)
    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)

    provider = provider_service.get_provider(seeded, provider_id)
    assert bridge_service.bridge_runtime(provider) is None
    status = client.get(f"/api/connect/bridges/{provider_id}").json()
    assert status["state"] == "failed" and "would have changed your project" in status["note"]
    # The person's own file is untouched, and the evidence is kept server-side.
    assert (project / "notes.md").read_text(encoding="utf-8") == "my own notes\n"
    after = snapshot(project)
    assert changed_files(before, after)["created"] == ["SCRATCH.txt"]
    evidence = json.loads((bridge_service.integration_dir(provider_id) / "validation-failed.json").read_text(encoding="utf-8"))
    assert evidence["evidence"]["created"] == ["SCRATCH.txt"]


def test_a_builder_that_finds_nothing_says_so(client, seeded, roots, builder):
    fake_builder.BEHAVIOUR["mode"] = "refuse"
    project = make_import_only_project(roots)
    body = connect_draft(client, project)
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)
    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)
    assert client.get(f"/api/connect/bridges/{provider_id}").json()["state"] == "failed"
    assert bridge_service.bridge_runtime(provider_service.get_provider(seeded, provider_id)) is None


# ----------------------------------------------------------------------------- among other runtimes

def test_a_native_runtime_outranks_a_built_connection(seeded, roots, builder):
    from app.connect.runtimes import rank
    from tests.test_runtime_fallback import cli_profile, http_profile

    manifest = {"bridge_version": 1, "command": ["python3", "bridge.py"], "source": {"fingerprint": "x"}, "built_by": "fixture-builder", "callable": "widget_brain.analysis.answer"}
    bridge = runtime_from_manifest(manifest)
    native = cli_profile("cli", roots)
    running = http_profile("running", 1)
    assert [rt.id for rt in rank([bridge, native, running])] == ["running", "cli", "bridge"]
    # Even a bridge with a perfect record stays behind a native way in.
    from adapters.runtime import HealthState, RuntimeHealth

    bridge.health = RuntimeHealth(state=HealthState.AVAILABLE)
    assert [rt.id for rt in rank([bridge, native])] == ["cli", "bridge"]
    # But it is still preferred to a native one that is known to be down.
    native.health = RuntimeHealth(state=HealthState.UNAVAILABLE, consecutive_failures=2)
    assert [rt.id for rt in rank([bridge, native])] == ["bridge", "cli"]


def test_rebuild_prefers_a_native_interface_that_has_appeared(client, seeded, roots, builder):
    project = make_import_only_project(roots)
    body = connect_draft(client, project)
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)
    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)
    provider = provider_service.get_provider(seeded, provider_id)
    assert bridge_service.bridge_runtime(provider) is not None

    # The project grows a command-line interface of its own.
    (project / "src" / "widget_brain" / "cli.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\np.add_argument("--query")\nif __name__ == "__main__":\n    print(p.parse_args().query)\n', encoding="utf-8"
    )
    (project / "README.md").write_text("# Widget Brain\n\nAnswers questions.\n\nRun `python -m widget_brain.cli`.\n", encoding="utf-8")
    note = bridge_service.rebuild(seeded, provider)
    seeded.refresh(provider)
    assert "its own connection" in note or "looking at the project again" in note
    carry_out(seeded, roots, 1)  # the worker looks again, where the project is
    seeded.refresh(provider)
    assert bridge_service.build_state(provider) is None  # nothing was built
    active = runtime_service.active_runtime(provider)
    assert active.kind == "python_entrypoint"  # native wins; the bridge stays as a fallback
    assert [rt.id for rt in runtime_service.runtimes_of(provider)] == ["cli", "bridge"]
    assert bridge_service.bridge_runtime(provider) is not None


def test_a_changed_project_marks_the_connection_for_review(seeded, roots, builder, client):
    project = make_import_only_project(roots)
    body = connect_draft(client, project)
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)
    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)
    provider = provider_service.get_provider(seeded, provider_id)
    assert bridge_service.review_state(provider) is None
    (project / "src" / "widget_brain" / "extra.py").write_text("def more():\n    return 2\n", encoding="utf-8")
    assert bridge_service.review_state(provider) == "needs_review"


def test_the_builder_may_write_only_into_bevros_own_folder(client, seeded, roots, builder):
    """The workspace it is given: write here, read the project, nothing else."""
    project = make_import_only_project(roots)
    body = connect_draft(client, project)
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)

    provider = provider_service.get_provider(seeded, provider_id)
    task = bridge_service.build_task(seeded, provider)
    run = task.runs[-1]
    request = task_service.build_request(seeded, run, secret_store=None)
    workspace = request.input["workspace"]
    assert workspace["path"] == str(bridge_service.integration_dir(provider_id))
    assert workspace["read_paths"] == [str(project)]
    assert workspace["permissions"] == ["read", "write", "run_commands"]
    # The project is never the place work happens, and the folder Bevro keeps
    # its before-picture in is not inside what the builder was given.
    assert str(project) != workspace["path"]
    snapshot_file = bridge_service._snapshot_path(provider_id)
    assert snapshot_file.is_file() and not str(snapshot_file).startswith(workspace["path"])
    # A workspace Bevro made for itself is not offered as one of the person's projects.
    assert all(w.id != uuid.UUID(workspace["id"]) for w in __import__("app.services.workspaces", fromlist=["x"]).list_workspaces(seeded))
    assert str(roots) not in client.get("/api/workspaces").text

    # What the coding provider is asked for carries the contract, not a design.
    assert "standard input" in task.original_request and str(project) in task.original_request
    assert "Never create, change, move or delete anything in the project" in task.original_request


def test_a_native_runtime_that_is_down_falls_back_into_the_built_connection(client, seeded, roots, builder):
    """A bridge is an ordinary runtime, so resilience covers it like any other."""
    from adapters.runtime import CredentialStrategy, Credentials, RuntimeKind
    from app.connect.runtimes import http_runtime

    project = make_import_only_project(roots)
    body = connect_draft(client, project)
    provider_id = uuid.UUID(client.post(f"/api/connect/drafts/{body['id']}/bridge").json()["provider_id"])
    carry_out(seeded, roots, 1)
    run_the_build_task(seeded)
    carry_out(seeded, roots, 1)
    provider = provider_service.get_provider(seeded, provider_id)
    bridge = bridge_service.bridge_runtime(provider)
    assert bridge is not None

    # The project later gains a service of its own - which happens to be down.
    native = http_runtime(
        "running",
        kind=RuntimeKind.PROCESS,
        adapter={"kind": "http", "config": {"base_url": "http://127.0.0.1:1", "invoke": {"method": "POST", "path": "/task", "body": {"prompt": "{request}"}}, "timeout_seconds": 2}},
        display_name="Already running on this machine",
        availability="ready",
        confidence="high",
        credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED),
        evidence=[],
    )
    runtime_service.set_runtimes(provider, [native, bridge], "running")
    seeded.flush()
    assert runtime_service.active_runtime(provider).id == "running"  # native is preferred

    record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    task = task_service.submit(seeded, "What changed in widgets?", provider=provider)
    seeded.commit()
    run = task.runs[-1]
    result, artifacts = task_service.execute(run.provider, task_service.build_request(seeded, run, secret_store=None), execution=run.execution)
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result, artifacts)

    assert task.state == "completed" and task.summary.startswith("Widget analysis for:")
    assert [a["runtime_id"] for a in run.meta["attempts"]] == ["running", "bridge"]
    assert run.meta["runtime"]["id"] == "bridge"
    assert client.get(f"/api/tasks/{task.id}").json()["runs"][-1]["recovered"] is True
