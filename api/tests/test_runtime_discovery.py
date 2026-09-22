"""Discovery finds every runtime an installation has, prefers the most native one,
keeps the project untouched, and asks for a credential only as a last resort."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext
from app.connect.strategies.local import LocalProjectStrategy
from app.connect.targets import classify_target
from tests import fixture_http_agent
from tests.connect_fixtures import make_managed_project, make_mcp_project, make_python_project, snapshot


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def discover(project: Path, root: Path, *, probe_host: bool = True):
    return ConnectionDiscoveryService([LocalProjectStrategy(probe_host=probe_host)], use_assist=False).discover(classify_target(str(project)), DiscoveryContext(roots=[root]))


def wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError("fixture server did not start")


@pytest.fixture
def running_agent(tmp_path):
    """Fixture A/C: the HTTP agent started as a process *from* a project folder, with its own credential."""
    root = tmp_path / "agents"
    port = free_port()
    project = make_managed_project(root, port=port, with_unit=True)
    env = {**os.environ, "FIXTURE_UPSTREAM_KEY": "the-agents-own-secret", "PYTHONPATH": str(Path(__file__).parent.parent)}
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "fixture_http_agent.py"), str(port)], cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_port(port)
        yield project, root, port
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_multiple_runtimes_found_and_the_running_service_wins(running_agent):
    project, root, port = running_agent
    before = snapshot(project)
    draft = discover(project, root)
    assert snapshot(project) == before  # zero touch, probes included
    kinds = {rt.id: rt.kind.value for rt in draft.runtimes}
    assert set(kinds.values()) >= {"process", "docker_compose", "systemd", "python_entrypoint"}, kinds
    active = draft.runtime
    assert active.kind in ("process", "docker_compose") and active.availability == "ready" and not draft.choice_needed
    assert active.display_name.startswith(("Already running on this machine", "Runs as a local service (already running)"))
    assert active.adapter["config"]["invoke"]["path"] == "/task" and active.adapter["config"]["invoke"]["body"] == {"prompt": "{request}"}
    assert draft.auth.required is False  # it has its own credential; Bevro never asks
    assert active.credentials.strategy in ("runtime_managed", "docker_environment")
    assert "fixture-secret-never-read" not in str(draft.model_dump()) and "the-agents-own-secret" not in str(draft.model_dump())
    unit = next(rt for rt in draft.runtimes if rt.kind == "systemd")
    assert not unit.invocable and unit.credentials.strategy == "systemd_environment"
    public = draft.public()
    assert public["runs_via"] == active.display_name and public["credentials_label"] == "Managed by provider"
    assert str(root) not in str(public)


def test_compose_service_not_running_is_connect_by_address_and_cli_wins_meanwhile(tmp_path):
    root = tmp_path / "agents"
    port = free_port()
    project = make_managed_project(root, port=port)
    draft = discover(project, root)
    kinds = {rt.kind.value for rt in draft.runtimes}
    assert kinds >= {"docker_compose", "python_entrypoint"} and "process" not in kinds
    compose = next(rt for rt in draft.runtimes if rt.kind == "docker_compose")
    assert compose.availability == "needs_start" and compose.credentials.strategy == "docker_environment"
    assert draft.runtime.kind == "python_entrypoint"  # the only thing that can take a task right now


def test_mcp_stdio_project_is_a_declared_interface(tmp_path):
    root = tmp_path / "agents"
    project = make_mcp_project(root)
    draft = discover(project, root, probe_host=False)
    assert draft.runtime.kind == "mcp_stdio" and draft.runtime.display_name == "Uses MCP on this machine"
    assert draft.runtime.adapter["config"]["argv"][1:] == ["-m", "notes_mcp.server"]
    assert draft.runtime.credentials.strategy == "none" and draft.auth.required is False


def test_cli_project_native_credential_beats_bevro_managed(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    project = make_python_project(root)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    draft = discover(project, root, probe_host=False)
    assert draft.runtime.credentials.strategy == "bevro_managed" and draft.auth.required
    monkeypatch.setenv("OPENAI_API_KEY", "on-the-host")
    draft = discover(project, root, probe_host=False)
    assert draft.runtime.credentials.strategy == "inherited_environment" and not draft.auth.required
    assert draft.public()["credentials_label"] == "Managed by provider"


def test_static_discovery_works_offline_without_probes(tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    draft = discover(project, root, probe_host=False)
    assert draft.runtime.kind == "python_entrypoint" and len(draft.runtimes) >= 1


def test_unknown_project_is_reported_plainly(tmp_path):
    root = tmp_path / "agents"
    (root / "photos").mkdir(parents=True)
    (root / "photos" / "IMG_1.txt").write_text("x", encoding="utf-8")
    draft = discover(root / "photos", root)
    assert not draft.invocable and draft.runtimes == [] and "doesn't look like" in draft.warnings[0]
