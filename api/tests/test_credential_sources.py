"""An existing agent keeps its own credential mechanism; Bevro asks only when nothing provides one."""

import uuid

from adapters import HealthResult, InvocationRequest, get_adapter
from adapters.command import build_env, credential_sources, missing_secrets
from app.connect.inspect import Project
from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext
from app.connect.targets import classify_target
from app.services import providers as provider_service
from app.services.providers import record_availability
from tests.connect_fixtures import make_python_project

CONFIG = {"secret_env": ["OPENAI_API_KEY"], "self_configured": []}


def discover(project, root):
    return ConnectionDiscoveryService(use_assist=False).discover(classify_target(str(project)), DiscoveryContext(roots=[root]))


def test_precedence_bevro_then_host_then_project_then_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert credential_sources(CONFIG, {}) == {"OPENAI_API_KEY": "missing"}
    assert missing_secrets(CONFIG, {}) == ["OPENAI_API_KEY"]
    assert credential_sources({**CONFIG, "self_configured": ["OPENAI_API_KEY"]}, {}) == {"OPENAI_API_KEY": "project"}
    monkeypatch.setenv("OPENAI_API_KEY", "from-the-worker-environment")
    assert credential_sources(CONFIG, {}) == {"OPENAI_API_KEY": "host"}
    assert build_env(CONFIG, {})["OPENAI_API_KEY"] == "from-the-worker-environment"
    assert credential_sources(CONFIG, {"OPENAI_API_KEY": "stored"}) == {"OPENAI_API_KEY": "bevro"}
    assert build_env(CONFIG, {"OPENAI_API_KEY": "stored"})["OPENAI_API_KEY"] == "stored"  # an explicit override wins


def test_env_var_presence_check_reads_names_only(tmp_path):
    (tmp_path / ".env").write_text("# comment\nexport OPENAI_API_KEY=sk-value-never-returned\nEMPTY=\n", encoding="utf-8")
    project = Project(tmp_path)
    assert project.declares_env_var("OPENAI_API_KEY") is True
    assert project.declares_env_var("EMPTY") is False
    assert project.declares_env_var("TELEGRAM_BOT_TOKEN") is False
    assert project.declares_env_var("../etc/passwd") is False
    assert project.read_text(".env") is None  # contents still never readable through the inspector


def test_project_with_its_own_dotenv_is_self_configured(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    project = make_python_project(root)
    (project / "pyproject.toml").write_text((project / "pyproject.toml").read_text().replace('"httpx>=0.27"', '"httpx>=0.27", "python-dotenv"'), encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    draft = discover(project, root)
    assert draft.auth.required is False and draft.auth.hint.startswith("Uses its own OpenAI credential")
    assert "Uses its own OpenAI credential (the project loads its .env)" in draft.evidence
    assert draft.adapter["config"]["self_configured"] == ["OPENAI_API_KEY"]
    assert "hunter2" not in str(draft.model_dump())  # the .env value stays in the project
    # Without a value in that .env the project cannot be self-configured, however it loads it.
    (project / ".env").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    assert discover(project, root).auth.required is True


def test_host_environment_counts_as_the_agents_own(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    project = make_python_project(root)
    monkeypatch.setenv("OPENAI_API_KEY", "set-in-the-worker-shell")
    draft = discover(project, root)
    assert draft.auth.required is False and any("already set on this machine" in e for e in draft.evidence)
    assert draft.runtime.credentials.strategy == "inherited_environment"
    assert draft.adapter["config"]["self_configured"] == []
    # And at run time the program simply inherits it; nothing is stored in Bevro.
    from tests.test_command_adapter import spec_for

    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    (project / "src" / "fixture_research" / "agent.py").write_text("import os\nprint('inherited' if os.environ.get('OPENAI_API_KEY') == 'set-in-the-worker-shell' else 'nope')\n", encoding="utf-8")
    from tests.test_command_adapter import run

    result = run(spec_for(project), InvocationRequest(task_id="t", run_id="r", request="q"))
    assert result.state == "completed" and result.artifacts[0].payload["text"] == "inherited"
    assert get_adapter("command").check(spec_for(project), {}).credentials == {"OPENAI_API_KEY": "host"}


def test_worker_report_tells_the_api_where_the_credential_comes_from(client, seeded, tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        project = make_python_project(root)
        body = client.post("/api/connect/discover", json={"target": str(project)}).json()
        provider = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={}).json()
        row = provider_service.get_provider(seeded, uuid.UUID(provider["id"]))
        # The API (in Docker) cannot see the host; the worker's report says the host has it.
        record_availability(seeded, row, HealthResult(ok=True, state="available", credentials={"OPENAI_API_KEY": "host"}))
        listed = next(p for p in client.get("/api/providers").json() if p["id"] == provider["id"])
        assert listed["credentials"] == [{"name": "OPENAI_API_KEY", "label": "OpenAI credential", "present": True, "source": "host", "status": "From this machine"}]
        record_availability(seeded, row, HealthResult(ok=True, state="available", credentials={"OPENAI_API_KEY": "missing"}))
        listed = next(p for p in client.get("/api/providers").json() if p["id"] == provider["id"])
        assert listed["credentials"][0]["status"] == "Missing"
    finally:
        get_settings.cache_clear()
