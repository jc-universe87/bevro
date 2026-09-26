"""Create: a sentence becomes an agent, through the same machinery as Connect.

Every builder here is a stand-in; no model is called.
"""

import json
import uuid
from pathlib import Path

import pytest

from adapters import HealthResult
from app.config import get_settings
from app.create.build import find_tests, scan_project, serious
from app.create.spec import AgentSpec, Permission
from app.create.specmodel import LLMSpecModel, RulesSpecModel, set_spec_model
from app.models import AgentBuild
from app.routing.catalogue import build_catalogue
from app.routing.deterministic import DeterministicRouter
from app.services import agents as agent_service
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services.providers import record_availability
from tests import fake_agent_builder

RESEARCH = "Research competitors in the note-taking app market and give me a short weekly report."
REVIEW = "Review pull requests and point out risky changes."
NOTES = "Categorise notes into work, family and admin."


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setenv("BEVRO_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("BEVRO_INTEGRATIONS_DIR", str(tmp_path / "integrations"))
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", "")
    get_settings.cache_clear()
    yield tmp_path / "agents"
    get_settings.cache_clear()


@pytest.fixture
def builder(seeded):
    fake_agent_builder.BEHAVIOUR["mode"] = "cli"
    provider = provider_service.register_provider(seeded, dict(fake_agent_builder.MANIFEST))
    seeded.flush()
    yield provider
    fake_agent_builder.BEHAVIOUR["mode"] = "cli"


def carry_out(seeded, times: int = 1) -> None:
    for _ in range(times):
        agent_service.run_pending(seeded)


def run_the_build_task(seeded) -> None:
    from app.domain.run_state import RunState

    for task in task_service.list_tasks(seeded, state="queued"):
        run = task.runs[-1]
        if run.state != RunState.PENDING:
            continue
        request = task_service.build_request(seeded, run, secret_store=None)
        result, artifacts = task_service.execute(run.provider, request)
        task_service.begin_run(seeded, run)
        task_service.finish_run(seeded, run, result, artifacts)


def create(client, seeded, description: str) -> dict:
    """The browser flow: preview, then build, then let the worker carry it out."""
    preview = client.post("/api/create/preview", json={"description": description}).json()
    assert preview["can_build"] is True, preview
    status = client.post("/api/create/build", json={"spec": preview["spec"], "description": description}).json()
    carry_out(seeded)
    run_the_build_task(seeded)
    carry_out(seeded)
    return {"preview": preview, "provider_id": status["provider_id"]}


# ----------------------------------------------------------------------------- the spec

def test_a_sentence_becomes_a_description_a_person_recognises():
    spec = RulesSpecModel().spec_for(RESEARCH)
    assert spec.name and len(spec.name.split()) <= 3
    assert {c.title for c in spec.capabilities} >= {"Research", "Competitor analysis"}
    assert Permission.WEB in spec.permissions and spec.output_expectation == "report"
    assert spec.schedule is None or "week" in spec.schedule.lower()
    view = spec.preview()
    assert view["needs"] and all(" " in n or n.isalpha() for n in view["needs"])
    assert "web" not in json.dumps(view).lower() or "Web access" in view["needs"]  # words, not codes


def test_the_model_path_is_structured_and_falls_back_to_rules():
    asked: list[str] = []

    class FakeModel:
        name = "fake"

        def structured(self, system, user, schema, name, max_tokens=400):
            asked.append(user)
            assert "code" not in system.lower() or "never write code" in system.lower()
            return {"name": "Market Watch", "description": "Tracks competitors in note-taking apps.",
                    "purpose": "Keep an eye on rivals.", "capabilities": [{"id": "research", "title": "Research"}],
                    "permissions": ["web"], "output_expectation": "report", "complexity": "small"}

    spec = LLMSpecModel(FakeModel()).spec_for(RESEARCH)
    assert spec.name == "Market Watch" and spec.source == "model" and spec.permissions == [Permission.WEB]
    assert RESEARCH in asked[0] and len(asked[0]) < 3000  # the sentence, nothing else

    class Broken:
        name = "broken"

        def structured(self, *a, **k):
            raise RuntimeError("no")

    spec = LLMSpecModel(Broken()).spec_for(RESEARCH)
    assert spec.source == "rules" and spec.capabilities  # Create still works offline


def test_a_builder_is_chosen_by_capability(seeded, builder):
    assert [p.slug for p in agent_service.builder_candidates(seeded)] == ["fixture-agent-builder"]
    builder.capabilities = [{"id": "gardening", "title": "Gardening"}]
    seeded.flush()
    assert not agent_service.can_build(seeded)
    builder.capabilities = [{"id": "software_build", "title": "Software build"}]
    seeded.flush()
    assert agent_service.can_build(seeded)


def test_without_a_builder_create_says_what_is_needed(client, seeded, managed):
    preview = client.post("/api/create/preview", json={"description": RESEARCH}).json()
    assert preview["can_build"] is False
    r = client.post("/api/create/build", json={"spec": preview["spec"]})
    assert r.status_code == 503 and r.json()["detail"] == agent_service.NO_BUILDER_MESSAGE


# ----------------------------------------------------------------------------- building the three agents

@pytest.mark.parametrize(
    ("description", "expect_capability"),
    [(RESEARCH, "research"), (REVIEW, "code_review"), (NOTES, "categorise")],
)
def test_three_kinds_of_agent_are_built_discovered_and_activated(client, seeded, managed, builder, description, expect_capability):
    made = create(client, seeded, description)
    provider = provider_service.get_provider(seeded, uuid.UUID(made["provider_id"]))

    status = client.get(f"/api/create/builds/{provider.id}").json()
    assert status["state"] == "ready" and status["note"] == "Created." and status["steps"][-1] == "Connecting"

    # Discovery - the ordinary kind - found how to run it.
    runtime = __import__("app.services.runtime", fromlist=["x"]).active_runtime(provider)
    assert runtime is not None and runtime.invocable and runtime.kind in ("python_entrypoint", "cli")
    assert runtime.adapter["kind"] == "command"
    build = agent_service.active_build(seeded, provider)
    assert build.state == "ready" and build.active and build.validation["ok"] is True
    assert build.validation["checks"][0] == "security scan" and build.validation["checks"][-2:] == ["discovery", "sample request"]
    assert build.validation["checks"][1] in ("its own tests", "tests not run on this machine")

    # Capabilities are the ones agreed with the person, not whatever was built.
    assert any(c["id"] == expect_capability for c in provider.capabilities), provider.capabilities
    assert {c["id"] for c in provider.capabilities} == {c["id"] for c in AgentSpec.model_validate(build.spec).capability_dicts()}

    # It is a real project that could be moved elsewhere.
    folder = agent_service.version_dir(provider.id, build.id)
    names = {p.name for p in folder.iterdir()}
    assert {"pyproject.toml", "README.md", "tests", "agent.json"} <= names
    assert (folder.parent.parent / "current").resolve() == folder.resolve()
    assert "bevro" not in (folder / "pyproject.toml").read_text().lower()

    # And an ordinary provider from here on.
    record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    listed = next(p for p in client.get("/api/providers").json() if p["id"] == str(provider.id))
    assert listed["origin"] == "created" and listed["actions"] == ["ask"]
    assert listed["build"]["version"] == 1 and listed["build"]["state"] == "ready"
    assert str(managed) not in json.dumps(listed)

    assert provider.slug in {e.id for e in build_catalogue(seeded)}  # the router sees it at once

    task = task_service.submit(seeded, f"{provider.name}: widgets", provider=provider)
    seeded.commit()
    run = task.runs[-1]
    result, artifacts = task_service.execute(run.provider, task_service.build_request(seeded, run, secret_store=None))
    task_service.begin_run(seeded, run)
    task_service.finish_run(seeded, run, result, artifacts)
    assert task.state == "completed" and "widgets" in (task.summary or "")
    assert [a.type for a in task.artifacts] == ["report"]


def test_the_creation_task_and_the_work_both_appear_under_recent(client, seeded, managed, builder):
    made = create(client, seeded, RESEARCH)
    provider = provider_service.get_provider(seeded, uuid.UUID(made["provider_id"]))
    record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    client.post("/api/tasks", json={"request": f"{provider.name}: widgets", "provider_id": str(provider.id)})
    titles = [t["title"] for t in client.get("/api/tasks").json()]
    assert any(t.startswith("Creating ") for t in titles) and any("widgets" in t for t in titles)


def test_the_router_picks_a_created_agent_by_capability(client, seeded, managed, builder):
    made = create(client, seeded, RESEARCH)
    provider = provider_service.get_provider(seeded, uuid.UUID(made["provider_id"]))
    record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    decision = DeterministicRouter().route(seeded, f"{provider.name}: what are competitors doing in note-taking apps?")
    assert decision.provider_id == provider.slug


# ----------------------------------------------------------------------------- when it goes wrong

def test_a_builder_that_fails_leaves_no_half_made_agent(client, seeded, managed, builder):
    fake_agent_builder.BEHAVIOUR["mode"] = "fail"
    preview = client.post("/api/create/preview", json={"description": RESEARCH}).json()
    status = client.post("/api/create/build", json={"spec": preview["spec"]}).json()
    carry_out(seeded)
    run_the_build_task(seeded)
    carry_out(seeded)

    provider = provider_service.get_provider(seeded, uuid.UUID(status["provider_id"]))
    out = client.get(f"/api/create/builds/{provider.id}").json()
    assert out["state"] == "failed" and out["note"] == agent_service.BUILD_FAILED_MESSAGE
    assert client.get(f"/api/providers/{provider.id}").json()["actions"] == []
    assert agent_service.active_build(seeded, provider) is None


def test_a_project_with_no_way_in_fails_validation(client, seeded, managed, builder):
    fake_agent_builder.BEHAVIOUR["mode"] = "empty"
    preview = client.post("/api/create/preview", json={"description": RESEARCH}).json()
    status = client.post("/api/create/build", json={"spec": preview["spec"]}).json()
    carry_out(seeded)
    run_the_build_task(seeded)
    carry_out(seeded)
    provider = provider_service.get_provider(seeded, uuid.UUID(status["provider_id"]))
    build = agent_service.latest_build(seeded, provider)
    assert build.state == "failed" and "couldn't find a way to run" in build.validation["reason"]
    assert agent_service.active_build(seeded, provider) is None


def test_a_hard_coded_secret_stops_the_build(client, seeded, managed, builder):
    fake_agent_builder.BEHAVIOUR["mode"] = "secret"
    preview = client.post("/api/create/preview", json={"description": RESEARCH}).json()
    status = client.post("/api/create/build", json={"spec": preview["spec"]}).json()
    carry_out(seeded)
    run_the_build_task(seeded)
    carry_out(seeded)
    provider = provider_service.get_provider(seeded, uuid.UUID(status["provider_id"]))
    build = agent_service.latest_build(seeded, provider)
    assert build.state == "failed" and "refused" in build.validation["reason"]
    assert build.validation["checks"] == ["security scan"]
    assert agent_service.active_build(seeded, provider) is None


def test_the_scan_catches_what_must_never_be_generated(tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "a.py").write_text('KEY = "sk-live-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    (folder / "b.py").write_text("import os\nos.system(cmd)\n", encoding="utf-8")
    (folder / "c.py").write_text('PATH = "/home/someone-else/notes"\n', encoding="utf-8")
    (folder / "d.py").write_text('KEY = os.environ["OPENAI_API_KEY"]\n', encoding="utf-8")
    problems = {f.file: f.problem for f in serious(scan_project(folder))}
    assert set(problems) == {"a.py", "b.py", "c.py"}
    assert "credential" in problems["a.py"] or "key" in problems["a.py"]
    assert find_tests(folder) is None  # no tests to run


def test_tests_that_cannot_be_run_here_do_not_fail_the_build(tmp_path, monkeypatch):
    """A missing test runner is this machine's business, not the agent's."""
    from app.create import build as build_module

    folder = tmp_path / "p"
    (folder / "tests").mkdir(parents=True)
    (folder / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    assert build_module.find_tests(folder) is not None  # this machine has a runner
    monkeypatch.setattr(build_module, "python_test_command", lambda: None)
    assert build_module.find_tests(folder) is None


# ----------------------------------------------------------------------------- rebuilding

def test_a_failed_rebuild_leaves_the_working_version_in_place(client, seeded, managed, builder):
    made = create(client, seeded, RESEARCH)
    provider = provider_service.get_provider(seeded, uuid.UUID(made["provider_id"]))
    first = agent_service.active_build(seeded, provider)
    first_runtime = __import__("app.services.runtime", fromlist=["x"]).active_runtime(provider)
    record_availability(seeded, provider, HealthResult(ok=True, state="available"))

    fake_agent_builder.BEHAVIOUR["mode"] = "secret"
    r = client.post(f"/api/create/builds/{provider.id}/rebuild", json={"description": "Research competitors and report weekly, with sources."})
    assert r.status_code == 201 and r.json()["state"] in ("designing", "building")
    # While it is being built, the agent still works.
    assert client.get(f"/api/providers/{provider.id}").json()["actions"] == ["ask"]
    carry_out(seeded)
    run_the_build_task(seeded)
    carry_out(seeded)

    seeded.refresh(provider)
    second = agent_service.latest_build(seeded, provider)
    assert second.build_number == 2 and second.state == "failed"
    assert agent_service.active_build(seeded, provider).id == first.id  # still the working one
    assert __import__("app.services.runtime", fromlist=["x"]).active_runtime(provider).adapter == first_runtime.adapter
    out = client.get(f"/api/create/builds/{provider.id}").json()
    assert out["state"] == "failed" and "still in use" in out["note"]
    assert client.get(f"/api/providers/{provider.id}").json()["actions"] == ["ask"]


def test_a_successful_rebuild_swaps_versions_and_keeps_the_old_one(client, seeded, managed, builder):
    made = create(client, seeded, RESEARCH)
    provider = provider_service.get_provider(seeded, uuid.UUID(made["provider_id"]))
    first = agent_service.active_build(seeded, provider)

    client.post(f"/api/create/builds/{provider.id}/rebuild", json={"description": "Research competitors and produce a concise weekly report with sources."})
    carry_out(seeded)
    run_the_build_task(seeded)
    carry_out(seeded)

    seeded.refresh(provider)
    second = agent_service.active_build(seeded, provider)
    assert second.build_number == 2 and second.id != first.id and second.state == "ready"
    assert not seeded.get(AgentBuild, first.id).active
    # Both versions are kept; `current` points at the new one.
    assert agent_service.version_dir(provider.id, first.id).is_dir()
    assert (agent_service.agent_dir(provider.id) / "current").resolve() == agent_service.version_dir(provider.id, second.id).resolve()
    listed = next(p for p in client.get("/api/providers").json() if p["id"] == str(provider.id))
    assert listed["build"]["version"] == 2


def test_editing_the_purpose_changes_what_is_built(client, seeded, managed, builder):
    made = create(client, seeded, NOTES)
    provider = provider_service.get_provider(seeded, uuid.UUID(made["provider_id"]))
    before = provider.description

    client.post(f"/api/create/builds/{provider.id}/rebuild", json={"description": "Categorise notes into work, family and admin, and summarise each group."})
    carry_out(seeded)
    run_the_build_task(seeded)
    carry_out(seeded)

    seeded.refresh(provider)
    spec = AgentSpec.model_validate(agent_service.active_build(seeded, provider).spec)
    assert provider.description != before and "summarise" in provider.description.lower()
    assert any(c.id == "summarise" for c in spec.capabilities)
    listed = next(p for p in client.get("/api/providers").json() if p["id"] == str(provider.id))
    assert listed["build"]["purpose"].startswith("Categorise notes")


def test_an_agent_that_needs_an_account_is_created_and_then_asks_for_it(client, seeded, managed, builder, monkeypatch):
    """Needing a credential is not a failed build: it is an account to connect."""
    from adapters import FailureKind, InvocationResult, ResultState
    from app.services import agents as service

    real = service._probe
    calls: list[str] = []

    def wants_credential(provider, runtime, folder):
        calls.append(runtime.kind.value)
        return False, "Research Agent needs a credential before it can run.", True

    monkeypatch.setattr(service, "_probe", wants_credential)
    made = create(client, seeded, RESEARCH)
    provider = provider_service.get_provider(seeded, uuid.UUID(made["provider_id"]))
    build = agent_service.active_build(seeded, provider)
    assert build is not None and build.state == "ready" and build.validation["needs_credential"] is True
    assert build.validation["checks"][-1] == "sample request (waiting for a credential)"
    assert client.get(f"/api/create/builds/{provider.id}").json()["state"] == "ready"
    assert calls and service._probe is not real
