"""Integration bridges: a small piece of Bevro-owned code that adapts a project
which has useful logic but no way in.

    external project        untouched, used as a dependency
          ↑
    Bevro-owned bridge      data/integrations/<provider-id>/bridge.py
          ↑
    RuntimeProfile          an ordinary CLI runtime speaking the JSON contract
          ↑
    ProviderRun

Nothing here runs a coding provider or writes code: it decides whether a
bridge is warranted, describes precisely what is wanted (BridgeSpec), and
checks afterwards that what came back is usable and that the project was left
alone. The building is ordinary Bevro work, handed to whichever provider
declares the capability.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.runtime import CredentialStrategy, Credentials, InputMode, OutputMode, RuntimeAbilities, RuntimeKind, RuntimeProfile
from app.connect.draft import ProviderDraft
from app.connect.inspect import Project
from app.connect.strategies.project import Finding

BRIDGE_VERSION = 1
BRIDGE_FILE = "bridge.py"
MANIFEST_FILE = "bridge.json"
# Evidence handed to the builder: names and small excerpts, never whole files.
MAX_ENTRYPOINTS = 12
MAX_EXCERPT = 1200
MAX_FILES_LISTED = 40
# Files that decide whether the project has changed underneath a bridge.
FINGERPRINT_SUFFIXES = (".py", ".js", ".mjs", ".ts", ".toml", ".cfg", ".json", ".txt")
FINGERPRINT_MAX_FILES = 4000

_PUBLIC_DEF = re.compile(r"^(?:async\s+)?def\s+([a-z][\w]*)\s*\(([^)]*)\)", re.M)
_PUBLIC_CLASS = re.compile(r"^class\s+([A-Z][\w]*)\s*[(:]", re.M)
_JS_EXPORT = re.compile(r"^\s*(?:export\s+(?:async\s+)?function\s+(\w+)|export\s+const\s+(\w+)\s*=|module\.exports(?:\.(\w+))?\s*=)", re.M)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- when a bridge is warranted

def needs_bridge(draft: ProviderDraft) -> bool:
    """True when the project is worth connecting but nothing in it can take a task.

    A native or managed runtime always wins: a bridge is only for a project
    that has code and no way in.
    """
    if any(rt.invocable for rt in draft.runtimes):
        return False
    evidence = draft.callable_evidence or {}
    return bool(evidence.get("modules") or evidence.get("exports"))


def callable_surface(project: Project, findings: list[Finding]) -> dict[str, Any]:
    """What could be called from outside, read statically: public functions and
    classes of the project's own packages, or a Node module's exports."""
    python = next((f for f in findings if f.kind == "python"), None)
    node = next((f for f in findings if f.kind == "node"), None)
    surface: dict[str, Any] = {"language": None, "modules": [], "exports": []}
    if python is not None:
        surface["language"] = "python"
        surface["modules"] = _python_callables(project)
    elif node is not None:
        surface["language"] = "node"
        surface["exports"] = _node_callables(project)
    return surface


def _package_dirs(project: Project) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for base in (".", "src"):
        if base != "." and not project.is_dir(base):
            continue
        for entry in project.listdir(base):
            rel = entry if base == "." else f"{base}/{entry}"
            if project.is_dir(rel) and project.is_file(f"{rel}/__init__.py") and not entry.endswith(".egg-info"):
                found.append((entry, rel))
    return found


def _python_callables(project: Project) -> list[dict[str, Any]]:
    """[{module, callables:[{name, kind, arguments}]}] for the project's packages."""
    modules: list[dict[str, Any]] = []
    for package, rel in _package_dirs(project):
        for name in project.listdir(rel):
            if not name.endswith(".py") or name.startswith("_") and name != "__init__.py":
                continue
            text = project.read_text(f"{rel}/{name}") or ""
            callables = [{"name": m.group(1), "kind": "function", "arguments": " ".join(m.group(2).split())[:120]} for m in _PUBLIC_DEF.finditer(text) if not m.group(1).startswith("_")]
            callables += [{"name": m.group(1), "kind": "class", "arguments": ""} for m in _PUBLIC_CLASS.finditer(text)]
            if callables:
                dotted = package if name == "__init__.py" else f"{package}.{name[:-3]}"
                modules.append({"module": dotted, "callables": callables[:8]})
            if len(modules) >= MAX_ENTRYPOINTS:
                return modules
    return modules


def _node_callables(project: Project) -> list[dict[str, Any]]:
    exports: list[dict[str, Any]] = []
    for rel in ("index.js", "index.mjs", "src/index.js", "lib/index.js", "index.ts", "src/index.ts"):
        text = project.read_text(rel)
        if text is None:
            continue
        names = [n for m in _JS_EXPORT.finditer(text) for n in m.groups() if n]
        if names:
            exports.append({"file": rel, "exports": sorted(set(names))[:8]})
        if len(exports) >= 4:
            break
    return exports


# --------------------------------------------------------------------------- the specification

@dataclass
class BridgeSpec:
    """Exactly what a builder is told. Bounded, and free of anything private."""

    provider_id: str
    provider_name: str
    description: str
    # Where the project is, on the machine that will build and run the bridge.
    project_path: str
    language: str
    # What the builder may look at: names, small excerpts, declared callables.
    evidence: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    # Where the bridge goes; the only place the builder may write.
    bridge_dir: str = ""
    interpreter: str | None = None
    permissions: list[str] = field(default_factory=lambda: ["read", "write", "run_commands"])
    restrictions: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        """Kept on the build task. No paths beyond the two it must name."""
        return {
            "provider_id": self.provider_id,
            "language": self.language,
            "capabilities": list(self.capabilities),
            "callables": self.evidence.get("modules") or self.evidence.get("exports") or [],
        }


DEFAULT_RESTRICTIONS = [
    "Write only inside the bridge folder. Never create, change, move or delete anything in the project.",
    "Never install packages into the project, change its configuration, or run git commit, git push or any other command that writes to it.",
    "Never read, copy or print credentials: no .env files, no key files, no tokens.",
    "Do not copy the project's source into the bridge. Import or call it where it already is.",
]


def build_spec(*, provider_id: str, provider_name: str, description: str, project_path: str, bridge_dir: str, project: Project, findings: list[Finding], capabilities: list[str], readme_excerpt: str | None = None) -> BridgeSpec:
    surface = callable_surface(project, findings)
    python = next((f for f in findings if f.kind == "python"), None)
    interpreter = None
    if surface["language"] == "python":
        for venv in (".venv", "venv"):
            program = project.venv_program(f"{venv}/bin/python")
            if program:
                interpreter = program
                break
    evidence: dict[str, Any] = {
        "files": [name for name in project.listdir() if not name.startswith(".")][:MAX_FILES_LISTED],
        "readme_excerpt": (readme_excerpt or "")[:MAX_EXCERPT],
        "dependencies": (python.dependencies if python else [])[:30],
        "package_layout": "src" if project.is_dir("src") else "flat",
    }
    evidence.update({k: v for k, v in surface.items() if k != "language" and v})
    return BridgeSpec(
        provider_id=provider_id,
        provider_name=provider_name,
        description=description,
        project_path=project_path,
        language=surface["language"] or "unknown",
        evidence=evidence,
        capabilities=capabilities,
        bridge_dir=bridge_dir,
        interpreter=interpreter,
        restrictions=list(DEFAULT_RESTRICTIONS),
    )


CONTRACT = """Bevro will run the bridge as a program in the bridge folder. It reads one JSON object on standard input:

{"request": "what the person asked, in plain words", "context": {}}

and prints exactly one JSON object on standard output:

{"status": "completed", "summary": "one plain sentence for a non-technical reader", "artifacts": [...]}

Each artifact is one of:
  {"type": "report", "title": "...", "text": "markdown"}
  {"type": "structured", "title": "...", "payload": {...}}
  {"type": "file", "title": "...", "path": "a file inside the bridge folder"}
  {"type": "deep_link", "title": "...", "url": "https://..."}

On failure print {"status": "failed", "summary": "what went wrong in plain words", "failure": "invocation_failed"}.
Print nothing else on standard output; diagnostics go to standard error."""


def build_prompt(spec: BridgeSpec) -> str:
    """The request the builder receives. Facts and a contract, not a design."""
    lines = [
        f"Write a small integration bridge so that Bevro can give work to an existing project called {spec.provider_name}.",
        "",
        f"What it does: {spec.description or 'not described'}.",
        f"Language: {spec.language}.",
        f"The project is at: {spec.project_path}",
        f"Write the bridge here, and nowhere else: {spec.bridge_dir}",
        "",
        "What Bevro knows about the project (found by reading it, never running it):",
        json.dumps(spec.evidence, ensure_ascii=False, indent=2)[:4000],
        "",
        "The contract the bridge must speak:",
        CONTRACT,
        "",
        "How to build it:",
        f"1. Create {BRIDGE_FILE} in the bridge folder. It must import or call the project where it already is"
        + (f" (use the interpreter {spec.interpreter} if you need the project's own environment)" if spec.interpreter else "")
        + ", pass the request to the most fitting public callable, and answer with the JSON contract above.",
        "2. Read the project first and choose the callable that best takes a plain request. If a callable needs arguments Bevro cannot supply, give them sensible defaults and say so in the summary.",
        f"3. Write {MANIFEST_FILE} in the bridge folder: {{\"entrypoint\": \"{BRIDGE_FILE}\", \"command\": [...the argv Bevro should run...], \"callable\": \"what you called\", \"notes\": \"anything a person should know\"}}. The command must run from the bridge folder.",
        "4. Add a short README.md saying what the bridge calls and how to test it.",
        "5. Test it yourself: run the bridge with a sample request on standard input and check that it prints one valid JSON object. Fix it until it does.",
        "",
        "Rules that matter more than finishing:",
        *[f"- {rule}" for rule in spec.restrictions],
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- fingerprints

def fingerprint(path: Path) -> str:
    """A stable digest of the files that decide whether a project has changed.

    Source and metadata only: results, logs and caches move all the time and
    say nothing about whether a bridge is still valid.
    """
    digest = hashlib.sha256()
    count = 0
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file.relative_to(path)
        if any(part.startswith(".") or part in ("node_modules", "__pycache__", "reports", "runs", "logs", "dist", "build") for part in rel.parts):
            continue
        if file.suffix.lower() not in FINGERPRINT_SUFFIXES:
            continue
        try:
            digest.update(str(rel).encode())
            digest.update(str(file.stat().st_size).encode())
        except OSError:
            continue
        count += 1
        if count >= FINGERPRINT_MAX_FILES:
            break
    return digest.hexdigest()


def snapshot(path: Path) -> dict[str, str]:
    """rel path -> content digest for every file, so a change can be named."""
    out: dict[str, str] = {}
    for file in sorted(p for p in path.rglob("*") if p.is_file() or p.is_symlink()):
        rel = str(file.relative_to(path))
        if any(part in ("node_modules", "__pycache__", ".git") for part in Path(rel).parts):
            continue
        try:
            if file.is_symlink():
                out[rel] = "link:" + os.readlink(file)
            else:
                out[rel] = hashlib.sha256(file.read_bytes()).hexdigest()
        except OSError:
            continue
        if len(out) > 20_000:
            break
    return out


def changed_files(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(rel for rel in set(before) & set(after) if before[rel] != after[rel])
    return {"created": created, "modified": modified, "deleted": deleted}


# --------------------------------------------------------------------------- manifest + runtime

def read_manifest(bridge_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((bridge_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_manifest(bridge_dir: Path, spec: BridgeSpec, *, command: list[str], builder: str, callable_used: str | None, fingerprint_value: str, validation: str, notes: str | None = None) -> dict[str, Any]:
    """Bevro's own record of the bridge. No secrets, no request text."""
    manifest = {
        "bridge_version": BRIDGE_VERSION,
        "provider_id": spec.provider_id,
        "source": {"path": spec.project_path, "language": spec.language, "fingerprint": fingerprint_value},
        "runtime_kind": RuntimeKind.CLI.value,
        "entrypoint": BRIDGE_FILE,
        "command": list(command),
        "input": InputMode.STDIN.value,
        "output": "json",
        "capabilities": list(spec.capabilities),
        "callable": callable_used,
        "built_by": builder,
        "created_at": _utcnow().isoformat(),
        "validation": validation,
        "notes": notes,
    }
    bridge_dir.mkdir(parents=True, exist_ok=True)
    (bridge_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def runtime_from_manifest(manifest: dict[str, Any], *, display_name: str = "Runs through a connection Bevro built") -> RuntimeProfile:
    """The bridge as an ordinary runtime. Nothing downstream knows it was generated."""
    command = [str(c) for c in manifest.get("command") or []]
    source = manifest.get("source") or {}
    return RuntimeProfile(
        id="bridge",
        kind=RuntimeKind.CLI,
        display_name=display_name,
        confidence="medium",
        availability="needs_worker",
        adapter={
            "kind": "command",
            "config": {
                "argv": command,
                # No cwd: each process resolves its own integration folder.
                "input": {"mode": "stdin", "format": "json"},
                "output": {"modes": ["json"]},
                "env": {},
                "secret_env": [],
                "timeout_seconds": 1800,
                "bridge": {"version": manifest.get("bridge_version"), "fingerprint": source.get("fingerprint"), "built_by": manifest.get("built_by")},
            },
        },
        credentials=Credentials(strategy=CredentialStrategy.INHERITED_ENVIRONMENT, note="Runs with the worker's own environment, like the project itself."),
        input=InputMode.STDIN,
        outputs=[OutputMode.JSON_RESPONSE, OutputMode.FILES],
        abilities=RuntimeAbilities(accepts_prompt=True, background=True, cancel=True, streaming=True, file_artifacts=True, health=True),
        evidence=[f"Bevro built this connection with {manifest.get('built_by') or 'a coding agent'}", f"It calls {manifest.get('callable') or 'the project'}"],
        warnings=[],
    )
