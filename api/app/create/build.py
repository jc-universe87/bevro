"""What a builder is told, and what Bevro checks when it answers.

The generated project is an ordinary project: a README, dependency metadata,
tests and a way to run it. Bevro's own discovery has to be able to find that
way — if it cannot, the build failed. Nothing here registers a runtime by hand.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.create.spec import AgentSpec, PERMISSION_WORDS, Permission

BUILD_VERSION = 1
AGENT_MANIFEST = "agent.json"
MAX_SCAN_FILES = 400
MAX_SCAN_BYTES = 200_000
TEXT_SUFFIXES = (".py", ".js", ".mjs", ".ts", ".json", ".toml", ".cfg", ".md", ".txt", ".sh", ".yml", ".yaml")

# What a generated project must not contain. Each is a real hazard, not a style
# preference: a secret baked into source, a path that only exists on this
# machine, or a way to run arbitrary shell from a request.
SECRET_PATTERNS = [
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|sk-proj-[A-Za-z0-9_-]{16,})"), "an OpenAI-style key"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"), "an Anthropic-style key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{12,}"), "an AWS key id"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"), "a hard-coded credential"),
]
SHELL_PATTERNS = [
    (re.compile(r"\bos\.system\s*\("), "os.system"),
    (re.compile(r"shell\s*=\s*True"), "shell=True"),
    (re.compile(r"\beval\s*\(|\bexec\s*\("), "eval/exec"),
    (re.compile(r"child_process\.exec\s*\("), "child_process.exec"),
]
_MACHINE_PATH = re.compile(r"(/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+|C:\\\\Users\\\\[A-Za-z0-9._-]+)")
# Paths that are fine to mention even though they look absolute.
_PATH_ALLOWED = re.compile(r"/home/(runner|user|agent)\b")


@dataclass
class BuildSpec:
    """Everything the builder is given. Bounded, and nothing else is granted."""

    provider_id: str
    build_id: str
    spec: AgentSpec
    # The only directory it may write in.
    target_dir: str
    # Reference material it may read, if any was approved. Usually empty.
    read_paths: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=lambda: ["read", "write", "run_commands"])

    def to_metadata(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "build_id": self.build_id, "spec_name": self.spec.name, "capabilities": [c.id for c in self.spec.capabilities]}


CONTRACT = """The project must be an ordinary, self-contained project that Bevro can discover and run. Specifically:

1. It must offer one obvious way to give it a request. In order of preference: an MCP server, a small HTTP service, or a command-line program. A command-line program is usually simplest and is a good default.
2. A request is one plain sentence. The answer is text, and may also produce files.
3. It must declare its metadata (pyproject.toml, package.json or equivalent), including any dependencies.
4. It must have a README.md saying what it does, how to run it, and which environment variables it needs.
5. It must have tests that pass without network access, run by a plain command stated in the README.
6. It must read any credential from an environment variable. Never write a key, token or password into a file.
7. It must not refer to any path outside its own directory, and must not run shell commands built from the request.
8. It must work if the whole folder is copied elsewhere: no dependency on Bevro itself."""


def build_prompt(spec: BuildSpec) -> str:
    """The request the builder receives: what is wanted, and the contract."""
    agent = spec.spec
    needs = [PERMISSION_WORDS[p] for p in agent.permissions]
    lines = [
        f"Build a small, self-contained agent called {agent.name}.",
        "",
        f"What it is for: {agent.purpose or agent.description}",
        f"In one sentence: {agent.description}",
        "",
        "It should be able to:",
        *[f"- {c.title}" + (f": {c.description}" if c.description else "") for c in agent.capabilities],
        "",
        f"A request to it looks like: {agent.input_expectation}",
        f"What it should hand back: {agent.output_expectation}",
        *([f"It will need: {', '.join(needs)}"] if needs else []),
        *([f"Outside services it may use: {', '.join(agent.integrations)}"] if agent.integrations else []),
        *(["It does not need to remember anything between requests."] if not agent.persistence else ["It may keep small state in its own folder."]),
        *(["Bevro decides when it runs, so do not build a scheduler or a loop into it."] if agent.schedule else []),
        "",
        f"Write it here, and nowhere else: {spec.target_dir}",
        "",
        "What the project must be:",
        CONTRACT,
        *([f"\nAlso: {c}" for c in agent.constraints] if agent.constraints else []),
        "",
        "Finish by running the tests yourself and fixing anything that fails.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- checks on what came back

@dataclass
class ScanFinding:
    file: str
    problem: str
    serious: bool


def scan_project(root: Path) -> list[ScanFinding]:
    """Read the generated source for the things that must never be there."""
    findings: list[ScanFinding] = []
    count = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root))
        if any(part in (".git", "node_modules", "__pycache__", ".venv", "venv") for part in Path(rel).parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        count += 1
        if count > MAX_SCAN_FILES:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_SCAN_BYTES]
        except OSError:
            continue
        for pattern, what in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(ScanFinding(rel, f"looks like {what} written into the source", True))
                break
        for pattern, what in SHELL_PATTERNS:
            if pattern.search(text):
                findings.append(ScanFinding(rel, f"runs arbitrary code or shell ({what})", True))
                break
        for match in _MACHINE_PATH.finditer(text):
            if not _PATH_ALLOWED.search(match.group(0)) and str(root) not in text[max(0, match.start() - 80):match.end() + 80]:
                findings.append(ScanFinding(rel, "refers to a path that only exists on this machine", True))
                break
    return findings


def serious(findings: list[ScanFinding]) -> list[ScanFinding]:
    return [f for f in findings if f.serious]


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if any(part in (".git", "node_modules", "__pycache__", ".venv") for part in rel.parts):
            continue
        try:
            digest.update(str(rel).encode())
            digest.update(str(path.stat().st_size).encode())
        except OSError:
            continue
    return digest.hexdigest()


def write_manifest(root: Path, *, provider_id: str, build_id: str, spec: AgentSpec, builder: str, runtime: str | None, validation: str) -> dict[str, Any]:
    """Bevro's own note about a build. The project stays independent of it."""
    manifest = {
        "build_version": BUILD_VERSION,
        "provider_id": provider_id,
        "build_id": build_id,
        "name": spec.name,
        "spec": spec.model_dump(mode="json"),
        "built_by": builder,
        "runtime": runtime,
        "validation": validation,
        "fingerprint": fingerprint(root),
    }
    (root / AGENT_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def python_test_command() -> list[str] | None:
    """How this machine can run a Python project's tests, if it can at all.

    An agent whose tests cannot be run here is not a broken agent: the runner
    simply is not installed. Bevro says so rather than blaming the build.
    """
    import subprocess

    for module, command in (("pytest", ["-m", "pytest", "-q"]), ("unittest", ["-m", "unittest", "discover", "-q"])):
        try:
            probe = subprocess.run(["python3", "-c", f"import {module}"], capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if probe.returncode == 0:
            return ["python3", *command]
    return None


def find_tests(root: Path) -> list[str] | None:
    """A plain command that runs the project's own tests, if it has any and
    this machine can run them."""
    if any(root.rglob("test_*.py")) or (root / "tests").is_dir():
        return python_test_command()
    if (root / "package.json").is_file():
        try:
            scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        if "test" in scripts and (root / "node_modules").is_dir():
            return ["npm", "test", "--silent"]
    return None
