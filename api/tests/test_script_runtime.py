"""A project whose only interface is an executable script is still discoverable.

Nothing here is language-specific: the script is shell, but a compiled binary
or any other program is found the same way.
"""

import uuid

from adapters import HealthResult, InvocationRequest, ProviderSpec, get_adapter
from app.config import get_settings
from app.connect.inspect import Project
from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext
from app.connect.strategies.local import LocalProjectStrategy
from app.connect.strategies.scripts import documented_commands, inspect_scripts
from app.connect.targets import classify_target
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services.providers import record_availability
from tests.connect_fixtures import make_python_project, make_script_project, snapshot


def discover(project, root):
    return ConnectionDiscoveryService([LocalProjectStrategy(probe_host=False)], use_assist=False).discover(classify_target(str(project)), DiscoveryContext(roots=[root]))


def test_documented_commands_are_read_from_the_readme():
    readme = '## Run\n\n```bash\n./bin/agent --topic "your question"\n```\n\nBuild with `./scripts/build.sh`.\n'
    assert documented_commands(readme) == {"bin/agent": '--topic "your question"', "scripts/build.sh": ""}


def test_executable_script_becomes_a_cli_runtime(tmp_path):
    root = tmp_path / "agents"
    project = make_script_project(root)
    before = snapshot(project)
    draft = discover(project, root)
    assert snapshot(project) == before

    assert draft.name == "Fixture Shell Agent" and draft.invocable
    rt = draft.runtime
    assert rt.kind == "cli" and rt.display_name == "Runs from this project" and rt.confidence == "high"
    assert rt.adapter["config"]["argv"] == ["./bin/agent"] and rt.adapter["config"]["input"] == {"mode": "argument"}
    assert rt.credentials.strategy == "none" and not draft.auth.required
    assert draft.invocation_label == './bin/agent "…"'
    # The build helper is not an interface for work.
    assert all("build" not in e.label for e in inspect_scripts(Project(project)).entrypoints[:1])
    assert "/bin/build.sh" not in str(draft.public())


def test_an_undocumented_script_is_less_certain(tmp_path):
    root = tmp_path / "agents"
    project = make_script_project(root, documented=False, name="quiet-agent")
    draft = discover(project, root)
    rt = draft.runtime
    assert rt.kind == "cli" and rt.confidence == "medium"  # named like an entry point, but nothing documents it
    assert any("couldn't tell how this program takes a request" in w for w in draft.warnings)


def test_a_wrapper_script_is_offered_alongside_a_language_entry_point(tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    (project / "bin").mkdir()
    wrapper = project / "bin" / "agent"
    wrapper.write_text("#!/usr/bin/env bash\nexec .venv/bin/python -m fixture_research.agent \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    draft = discover(project, root)
    kinds = [(rt.kind.value, rt.adapter["config"]["argv"][-1]) for rt in draft.runtimes]
    assert ("python_entrypoint", "fixture_research.agent") in kinds
    assert any(kind == "cli" and argv == "./bin/agent" for kind, argv in kinds)
    assert draft.runtime.kind == "python_entrypoint"  # the declared module beats a guessed wrapper


def test_script_project_connects_and_runs_like_any_other(client, seeded, tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    get_settings.cache_clear()
    try:
        project = make_script_project(root)
        body = client.post("/api/connect/discover", json={"target": str(project)}).json()
        assert body["draft"]["runs_via"] == "Runs from this project" and body["draft"]["credentials_label"] == "None needed"
        provider = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={}).json()
        row = provider_service.get_provider(seeded, uuid.UUID(provider["id"]))
        record_availability(seeded, row, HealthResult(ok=True, state="available"))

        task = task_service.submit(seeded, "Fixture Shell Agent: what changed in the widget market?")
        seeded.commit()
        run = task.runs[-1]
        request = task_service.build_request(seeded, run, secret_store=None)
        result, artifacts = task_service.execute(run.provider, request)
        task_service.begin_run(seeded, run)
        task_service.finish_run(seeded, run, result, artifacts)
        assert task.state == "completed"
        assert task.summary == "answering: Fixture Shell Agent: what changed in the widget market?"
        assert [(a.type, a.title) for a in task.artifacts] == [("report", "Output from Fixture Shell Agent")]
    finally:
        get_settings.cache_clear()
