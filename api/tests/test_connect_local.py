"""Local project discovery: zero-touch, inside approved roots only."""

import os
from pathlib import Path

import pytest

from adapters.localroots import OutsideRoots, find_by_name, parse_roots, resolve_within
from app.connect.draft import ProviderDraft
from app.connect.inspect import Project
from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed
from app.connect.strategies.docker import inspect_docker
from app.connect.strategies.node import inspect_node
from app.connect.strategies.python import inspect_python
from app.connect.targets import classify_target
from tests.connect_fixtures import make_docker_project, make_node_project, make_python_project, snapshot


def discover(text: str, roots: list[Path]) -> ProviderDraft:
    return ConnectionDiscoveryService(use_assist=False).discover(classify_target(text), DiscoveryContext(roots=roots))


# ----------------------------------------------------------------------------- roots

def test_paths_outside_the_roots_and_traversal_are_rejected(tmp_path):
    root = tmp_path / "agents"
    root.mkdir()
    (tmp_path / "private").mkdir()
    assert resolve_within(str(root / "."), [root]) == root
    with pytest.raises(OutsideRoots):
        resolve_within(str(tmp_path / "private"), [root])
    with pytest.raises(OutsideRoots):
        resolve_within(str(root / ".." / "private"), [root])
    with pytest.raises(OutsideRoots):
        resolve_within("relative/path", [root])
    with pytest.raises(OutsideRoots):
        resolve_within(str(root), [])  # no roots configured: nothing is allowed


def test_symlinks_cannot_escape_a_root(tmp_path):
    root = tmp_path / "agents"
    root.mkdir()
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    os.symlink(secret_dir, root / "escape")
    with pytest.raises(OutsideRoots):
        resolve_within(str(root / "escape"), [root])
    assert find_by_name("escape", [root]) is None
    assert find_by_name("../secret", [root]) is None


def test_parse_roots_expands_and_drops_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "agents").mkdir()
    roots = parse_roots("~/agents:/definitely/not/here, ")
    assert roots == [(tmp_path / "agents").resolve()]


# ----------------------------------------------------------------------------- python

def test_python_project_is_discovered_and_left_untouched(tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    before = snapshot(project)

    draft = discover(str(project), [root])

    assert snapshot(project) == before  # byte-for-byte, including symlinks
    assert draft.name == "Fixture Research Agent"
    assert draft.description.startswith("Researches competitor moves")
    assert draft.mechanism == "command" and draft.invocable
    assert draft.confidence == "high"
    assert {c.id for c in draft.capabilities} >= {"research", "competitor_analysis", "product_strategy", "market_analysis"}
    assert draft.adapter["kind"] == "command"
    config = draft.adapter["config"]
    assert config["argv"] == [str(project / ".venv" / "bin" / "python"), "-m", "fixture_research.agent"]
    assert config["cwd"] == str(project)
    assert config["input"] == {"mode": "flag", "flag": "--topic"}
    assert config["secret_env"] == ["OPENAI_API_KEY"]
    assert draft.auth.required and draft.auth.secret_name == "OPENAI_API_KEY"
    assert "README documents python -m fixture_research.agent" in " ".join(draft.evidence)
    assert draft.runtime is not None and draft.runtime.kind == "python_entrypoint" and draft.runtime.display_name == "Runs from this project"
    assert draft.runtime.credentials.strategy == "bevro_managed" and draft.runtime.credentials.required_from_user


def test_python_discovery_never_reads_env_and_public_view_has_no_paths(tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root, env_secret="THE-SECRET-VALUE")
    draft = discover(str(project), [root])
    public = str(draft.public())
    assert "THE-SECRET-VALUE" not in public and "THE-SECRET-VALUE" not in str(draft.model_dump())
    assert str(tmp_path) not in public
    assert "adapter" not in draft.public()
    assert draft.public()["invocation_label"] == 'python -m fixture_research.agent --topic "…"'
    assert Project(project).read_text(".env") is None  # the reader refuses credential files outright
    assert Project(project).read_text(".env.example") is not None


def test_bare_folder_name_is_looked_up_under_the_roots(tmp_path):
    root = tmp_path / "agents"
    make_python_project(root)
    assert discover("fixture-research", [root]).name == "Fixture Research Agent"
    with pytest.raises(DiscoveryFailed):
        discover("no-such-folder", [root])


def test_python_project_without_an_entry_point_is_honest(tmp_path):
    root = tmp_path / "agents"
    project = root / "library"
    (project / "lib").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "library"\ndescription = "A helper library"\n', encoding="utf-8")
    (project / "lib" / "__init__.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    draft = discover(str(project), [root])
    assert not draft.invocable and draft.confidence == "low"
    assert "doesn't expose a connection Bevro can use yet" in draft.warnings[0]
    # It has code worth reaching, so Bevro can offer to build a connection of its own.
    assert draft.needs_bridge and draft.callable_evidence["modules"][0]["module"] == "lib"
    assert draft.public()["needs_bridge"] is True


def test_python_web_service_is_connected_by_address_not_started(tmp_path):
    root = tmp_path / "agents"
    project = root / "svc"
    project.mkdir(parents=True)
    (project / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    (project / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    draft = discover(str(project), [root])
    assert draft.mechanism == "http" and draft.availability == "needs_start"
    assert draft.adapter["config"]["base_url"] == "http://localhost:8000"
    assert "won't start it" in draft.warnings[-1]


def test_python_inspector_finds_declared_scripts_and_flags(tmp_path):
    project = tmp_path / "p"
    (project / "pkg").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "pkg"\n[project.scripts]\npkg-cli = "pkg.cli:main"\n', encoding="utf-8")
    (project / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (project / "pkg" / "cli.py").write_text('import argparse\np = argparse.ArgumentParser()\np.add_argument("--query")\nif __name__ == "__main__":\n    p.parse_args()\n', encoding="utf-8")
    finding = inspect_python(Project(project))
    assert finding is not None
    labels = {e.label: e for e in finding.entrypoints}
    assert "python -m pkg.cli" in labels
    assert labels["python -m pkg.cli"].input == {"mode": "flag", "flag": "--query"}


# ----------------------------------------------------------------------------- node

def test_node_project_metadata(tmp_path):
    root = tmp_path / "agents"
    project = make_node_project(root)
    before = snapshot(project)
    draft = discover(str(project), [root])
    assert snapshot(project) == before
    assert draft.name == "Fixture Notes" and draft.mechanism == "command" and draft.runtime.kind == "node_entrypoint"
    assert draft.adapter["config"]["argv"] == ["node", "bin/cli.js"]
    assert "package.json declares the command 'fixture-notes'" in " ".join(draft.evidence)
    assert any("not installed" in w for w in draft.warnings)  # no node_modules; Bevro will not npm install


def test_node_mcp_server_is_drafted_as_mcp(tmp_path):
    root = tmp_path / "agents"
    project = make_node_project(root, mcp=True, name="notes-mcp")
    finding = inspect_node(Project(project))
    assert "mcp" in finding.frameworks
    draft = discover(str(project), [root])
    assert draft.mechanism == "mcp" and draft.adapter["kind"] == "mcp"
    assert draft.adapter["config"]["argv"] == ["node", "bin/cli.js"]
    assert any("Test connection" in w for w in draft.warnings)


# ----------------------------------------------------------------------------- docker

def test_docker_compose_metadata_without_starting_anything(tmp_path):
    root = tmp_path / "agents"
    project = make_docker_project(root)
    finding = inspect_docker(Project(project))
    assert finding.services[0]["port"] == 9876 and finding.services[0]["health_path"] == "/healthz"
    assert finding.services[0]["service"] == "api" and finding.services[0]["depends_on"] == ["db"]
    draft = discover(str(project), [root])
    assert draft.mechanism == "http" and draft.availability == "needs_start"
    assert draft.adapter["config"] == {"base_url": "http://localhost:9876", "health_path": "/healthz"}
    assert "docker compose up -d" in draft.warnings[-1]


def test_unknown_folder_kind_is_reported_plainly(tmp_path):
    root = tmp_path / "agents"
    (root / "photos").mkdir(parents=True)
    (root / "photos" / "a.txt").write_text("hi", encoding="utf-8")
    draft = discover(str(root / "photos"), [root])
    assert not draft.invocable and "doesn't look like a Python, Node or Docker project" in draft.warnings[0]


def test_discovery_tolerates_any_shape_of_project_metadata(tmp_path):
    """Bevro reads files other people wrote: an unexpected shape must not crash it."""
    root = tmp_path / "agents"
    for name, packages in (("listed", '["thing"]'), ("stringy", '"thing"'), ("table", '{find = {where = ["src"]}}')):
        project = root / name
        (project / "thing").mkdir(parents=True)
        (project / "thing" / "__init__.py").write_text("", encoding="utf-8")
        (project / "thing" / "cli.py").write_text('import argparse\np = argparse.ArgumentParser()\np.add_argument("--request")\nif __name__ == "__main__":\n    print(p.parse_args().request)\n', encoding="utf-8")
        (project / "pyproject.toml").write_text(f'[project]\nname = "{name}"\ndescription = "A thing"\n\n[tool.setuptools]\npackages = {packages}\n', encoding="utf-8")
        draft = discover(str(project), [root])
        assert draft.runtime is not None, name
        assert draft.runtime.adapter["config"]["argv"][1:] == ["-m", "thing.cli"], name
