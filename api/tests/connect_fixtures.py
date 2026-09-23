"""Temporary projects for Connect tests. Nothing here depends on a private project."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import textwrap
from pathlib import Path

AGENT_SOURCE = textwrap.dedent(
    '''
    """Fixture agent: takes --topic, writes a report into its own reports/ folder."""
    import argparse
    from pathlib import Path


    def main() -> None:
        parser = argparse.ArgumentParser(description="Fixture research agent")
        parser.add_argument("--topic", required=True)
        parser.add_argument("--no-presentation", action="store_true")
        args = parser.parse_args()
        reports = Path(__file__).resolve().parents[2] / "reports"
        reports.mkdir(exist_ok=True)
        path = reports / "latest-findings.md"
        path.write_text(f"# Findings\\n\\nTopic: {args.topic}\\n", encoding="utf-8")
        print("Working on:", args.topic)
        print(f"Report written to reports/{path.name}")


    if __name__ == "__main__":
        main()
    '''
)


def make_python_project(root: Path, *, name: str = "fixture-research", venv: bool = True, env_secret: str = "hunter2-do-not-leak") -> Path:
    project = root / name
    (project / "src" / "fixture_research").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{name}"
            version = "0.9.0"
            description = "Fixture Research Agent"
            requires-python = ">=3.11"
            dependencies = ["openai-agents", "httpx>=0.27"]

            [tool.setuptools.packages.find]
            where = ["src"]
            """
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        textwrap.dedent(
            """
            # Fixture Research Agent

            Researches competitor moves in the widget market and recommends a product strategy.

            ## Run

            ```bash
            python -m fixture_research.agent --topic "What changed?"
            ```

            ## Competitor watch

            A weekly competitor watch keeps an eye on the market. Competitor changes are verified before reporting.
            """
        ),
        encoding="utf-8",
    )
    (project / "src" / "fixture_research" / "__init__.py").write_text("", encoding="utf-8")
    (project / "src" / "fixture_research" / "agent.py").write_text(AGENT_SOURCE, encoding="utf-8")
    (project / ".env").write_text(f"OPENAI_API_KEY={env_secret}\n", encoding="utf-8")
    (project / ".env.example").write_text("OPENAI_API_KEY=\nTELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    if venv:
        (project / ".venv" / "bin").mkdir(parents=True)
        os.symlink(sys.executable, project / ".venv" / "bin" / "python")
    return project


def make_node_project(root: Path, *, mcp: bool = False, name: str = "fixture-notes") -> Path:
    project = root / name
    project.mkdir(parents=True)
    pkg = {
        "name": name,
        "version": "1.2.0",
        "description": "Summarise and search your notes",
        "bin": {"fixture-notes": "bin/cli.js"},
        "main": "index.js",
        "scripts": {"start": "node index.js", "test": "vitest"},
        "dependencies": {"@modelcontextprotocol/sdk": "^1.0.0"} if mcp else {"commander": "^12.0.0"},
    }
    (project / "package.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    (project / "bin").mkdir()
    (project / "bin" / "cli.js").write_text("#!/usr/bin/env node\nconsole.log('hi');\n", encoding="utf-8")
    (project / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    (project / "README.md").write_text("# Fixture Notes\n\nSummarise and search your notes from the terminal.\n", encoding="utf-8")
    return project


def make_docker_project(root: Path, *, name: str = "fixture-service") -> Path:
    project = root / name
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(
        textwrap.dedent(
            """
            services:
              db:
                image: postgres:16-alpine
                ports:
                  - "5433:5432"
              api:
                build: .
                ports:
                  - "9876:8000"
                healthcheck:
                  test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
                depends_on:
                  - db
            """
        ),
        encoding="utf-8",
    )
    (project / "Dockerfile").write_text("FROM python:3.12-slim\nEXPOSE 8000\nCMD [\"uvicorn\", \"app:app\"]\n", encoding="utf-8")
    (project / "README.md").write_text("# Fixture Service\n\nA quotes service for the sales desk.\n", encoding="utf-8")
    return project


# Directories and files the *interpreter* creates as it runs, which are not
# part of a project and never a modification of one. CPython writes these
# unless PYTHONDONTWRITEBYTECODE is set, which a bare CI runner does not set.
# Nothing else is ignored: a project Bevro touched must still show up.
INTERPRETER_CACHE_DIRS = frozenset({"__pycache__"})
INTERPRETER_CACHE_SUFFIXES = (".pyc", ".pyo")


def is_interpreter_cache(rel: Path) -> bool:
    """True for CPython's own bytecode cache, wherever it sits in the tree."""
    return bool(INTERPRETER_CACHE_DIRS.intersection(rel.parts)) or rel.suffix in INTERPRETER_CACHE_SUFFIXES


def snapshot(path: Path) -> dict[str, str]:
    """rel path -> sha256 of every file (symlinks by target), for byte-for-byte comparisons.

    Running a project compiles it, and CPython caches the bytecode beside the
    source. That is the interpreter's doing, not Bevro's, so it is left out
    here - and only that. Any real file a project gained, lost or changed is
    still compared byte for byte.
    """
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(path):
        # Do not descend into the bytecode cache at all.
        dirnames[:] = sorted(d for d in dirnames if d not in INTERPRETER_CACHE_DIRS)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(path)
            if is_interpreter_cache(rel):
                continue
            key = str(rel)
            if full.is_symlink():
                out[key] = "link:" + os.readlink(full)
            else:
                out[key] = hashlib.sha256(full.read_bytes()).hexdigest()
    return out


def make_mcp_project(root: Path, *, name: str = "fixture-notes-mcp") -> Path:
    """Fixture C (MCP flavour): a Python project whose entry point speaks MCP on stdio."""
    project = root / name
    (project / "src" / "notes_mcp").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{name}"
            version = "0.1.0"
            description = "Notes assistant exposed as an MCP server"
            dependencies = ["mcp"]

            [tool.setuptools.packages.find]
            where = ["src"]
            """
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# Notes MCP\n\nSearch and summarise your notes over MCP.\n\nRun with `python -m notes_mcp.server`.\n", encoding="utf-8")
    (project / "src" / "notes_mcp" / "__init__.py").write_text("", encoding="utf-8")
    fake = Path(__file__).parent / "fake_mcp_server.py"
    source = fake.read_text(encoding="utf-8").replace('"""A tiny MCP server', '"""from mcp.server import FastMCP  # (fixture: speaks MCP by hand)\n\nA tiny MCP server')
    (project / "src" / "notes_mcp" / "server.py").write_text(source, encoding="utf-8")
    (project / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, project / ".venv" / "bin" / "python")
    return project


def make_managed_project(root: Path, *, port: int, name: str = "fixture-desk", with_unit: bool = False, with_cli: bool = True) -> Path:
    """Fixture C (managed flavour): a project run as a Compose service on `port`,
    with its own environment; optionally a systemd unit and a raw CLI as well."""
    project = root / name
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(
        textwrap.dedent(
            f"""
            services:
              desk:
                build: .
                ports:
                  - "{port}:8000"
                env_file: .env
                environment:
                  - FIXTURE_UPSTREAM_KEY
                healthcheck:
                  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
            """
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# Fixture Desk\n\nA research and drafting desk for widgets, run as a service.\n", encoding="utf-8")
    (project / ".env").write_text("FIXTURE_UPSTREAM_KEY=fixture-secret-never-read\n", encoding="utf-8")
    if with_cli:
        (project / "pyproject.toml").write_text('[project]\nname = "fixture-desk"\ndescription = "Fixture Desk"\ndependencies = ["openai"]\n', encoding="utf-8")
        (project / "desk").mkdir()
        (project / "desk" / "__init__.py").write_text("", encoding="utf-8")
        (project / "desk" / "cli.py").write_text('import argparse\np = argparse.ArgumentParser()\np.add_argument("--prompt")\nif __name__ == "__main__":\n    print(p.parse_args().prompt)\n', encoding="utf-8")
    if with_unit:
        (project / "deploy").mkdir()
        (project / "deploy" / "fixture-desk.service").write_text(
            "[Unit]\nDescription=Fixture desk\n[Service]\nType=oneshot\nEnvironmentFile=/etc/fixture-desk/credentials\nWorkingDirectory=/srv/fixture-desk\nExecStart=/srv/fixture-desk/.venv/bin/python -m desk.cli\n", encoding="utf-8"
        )
    return project


def make_script_project(root: Path, *, name: str = "fixture-shell-agent", documented: bool = True) -> Path:
    """Fixture D: no Python or Node metadata at all - just an executable script,
    the way many in-house agents are shipped."""
    project = root / name
    (project / "bin").mkdir(parents=True)
    agent = project / "bin" / "agent"
    agent.write_text('#!/usr/bin/env bash\nprintf "answering: %s\\n" "$1"\n', encoding="utf-8")
    agent.chmod(0o755)
    chore = project / "bin" / "build.sh"
    chore.write_text("#!/usr/bin/env bash\necho build\n", encoding="utf-8")
    chore.chmod(0o755)
    usage = '```bash\n./bin/agent "your question"\n```\n' if documented else ""
    (project / "README.md").write_text(f"# Fixture Shell Agent\n\nResearches the widget market and reports what changed.\n\n## Run\n\n{usage}", encoding="utf-8")
    return project


def make_import_only_project(root: Path, *, name: str = "fixture-library") -> Path:
    """Fixture A: useful Python logic, a public callable, and no way in at all."""
    project = root / name
    (project / "src" / "widget_brain").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{name}"
            version = "0.3.0"
            description = "Answers questions about the widget market"

            [tool.setuptools.packages.find]
            where = ["src"]
            """
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# Widget Brain\n\nAnswers questions about the widget market and competitor moves.\n", encoding="utf-8")
    (project / "src" / "widget_brain" / "__init__.py").write_text("", encoding="utf-8")
    (project / "src" / "widget_brain" / "analysis.py").write_text(
        'def answer(question: str) -> str:\n    """Answer a question about widgets."""\n    return f"Widget analysis for: {question}"\n\n\nclass Report:\n    pass\n',
        encoding="utf-8",
    )
    return project


def make_module_only_node_project(root: Path, *, name: str = "fixture-node-library") -> Path:
    """Fixture B: a Node module with an export and no CLI or server."""
    project = root / name
    project.mkdir(parents=True)
    (project / "package.json").write_text(json.dumps({"name": name, "version": "1.0.0", "description": "Summarises notes", "main": "index.js"}, indent=2), encoding="utf-8")
    (project / "index.js").write_text("module.exports.summarise = (text) => `Summary of: ${text}`;\n", encoding="utf-8")
    (project / "README.md").write_text("# Note Summariser\n\nSummarises notes. Used as a library.\n", encoding="utf-8")
    return project


def make_empty_project(root: Path, *, name: str = "fixture-nothing") -> Path:
    """Fixture C: a project with metadata but nothing callable."""
    project = root / name
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "fixture-nothing"\ndescription = "Just some data"\n', encoding="utf-8")
    (project / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (project / "README.md").write_text("# Nothing Much\n\nA folder of data.\n", encoding="utf-8")
    return project
