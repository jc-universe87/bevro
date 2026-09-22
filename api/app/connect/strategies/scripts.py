"""Executable scripts as an entry point, in any language.

A project's interface is sometimes just a program: `./bin/agent "question"`,
`./run.sh`, a compiled binary. This inspector finds files with the executable
bit at the project root and under the conventional folders, and cross-checks
them against launch commands the README documents. Nothing is executed, and
no file's contents are read beyond a shebang line and the README.
"""

from __future__ import annotations

import re

from app.connect.inspect import Project
from app.connect.strategies.project import INPUT_FLAGS, Entrypoint, Finding

SCRIPT_DIRS = (".", "bin", "scripts", "cmd")
MAX_SCRIPTS = 12
# Names that suggest the way in, best first.
ENTRY_NAMES = ("agent", "run", "start", "cli", "main", "serve", "ask", "query", "app")
# Housekeeping, not an interface for work.
CHORE_NAMES = re.compile(r"^(install|setup|bootstrap|build|compile|test|tests|lint|format|fmt|check|ci|release|publish|deploy|migrate|clean|update|upgrade|backup|restore|sync|watch|entrypoint|docker-entrypoint|pre-commit|post-.*|activate)([._-].*)?$", re.I)
SKIP_SUFFIXES = (".so", ".dylib", ".dll", ".o", ".a", ".pyc", ".lock", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini")
# `./bin/agent "a question"`, `bin/agent --topic "..."`, `./run.sh <question>`
_DOCUMENTED = re.compile(r"(?:^|[\s`$])(\.?/?(?:bin|scripts|cmd)/[\w.-]+|\./[\w.-]+)((?:\s+--?[\w-]+)?(?:\s+(?:\"[^\"]*\"|'[^']*'|<[\w -]+>|\$\{?[A-Z_]+\}?))?)", re.M)
_SHEBANG = re.compile(r"^#!\s*\S+")


def _is_script(project: Project, rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name.startswith(".") or name.lower().endswith(SKIP_SUFFIXES) or CHORE_NAMES.match(name):
        return False
    return project.is_executable(rel)


def _rank(rel: str) -> int:
    stem = rel.rsplit("/", 1)[-1].split(".")[0].lower()
    return ENTRY_NAMES.index(stem) if stem in ENTRY_NAMES else len(ENTRY_NAMES)


def documented_commands(readme: str) -> dict[str, str]:
    """{script path as written: the arguments shown after it} from the README."""
    found: dict[str, str] = {}
    for match in _DOCUMENTED.finditer(readme):
        path = match.group(1).lstrip("./")
        found.setdefault(path, " ".join(match.group(2).split()))
    return found


def _input_for(arguments: str) -> tuple[dict[str, str], bool]:
    """How the request is passed, from the usage the README shows."""
    words = arguments.split()
    flag = next((w for w in words if w in INPUT_FLAGS), None)
    if flag:
        return {"mode": "flag", "flag": flag}, True
    if arguments.strip():  # a quoted example or a <placeholder> after the command
        return {"mode": "argument"}, True
    return {"mode": "argument"}, False


def inspect_scripts(project: Project) -> Finding | None:
    """Executable entry points. Returns None when the project has none."""
    candidates: list[str] = []
    for folder in SCRIPT_DIRS:
        if folder != "." and not project.is_dir(folder):
            continue
        for name in project.listdir(folder):
            rel = name if folder == "." else f"{folder}/{name}"
            if _is_script(project, rel):
                candidates.append(rel)
    if not candidates:
        return None

    finding = Finding(kind="script")
    readme = ""
    for candidate in ("README.md", "README.rst", "README.txt", "README"):
        readme = project.read_text(candidate, 32_000) or ""
        if readme:
            break
    documented = documented_commands(readme)
    candidates.sort(key=lambda rel: (0 if rel.lstrip("./") in documented else 1, _rank(rel), rel))

    for rel in candidates[:MAX_SCRIPTS]:
        arguments = documented.get(rel.lstrip("./"))
        interpreted = bool(_SHEBANG.match((project.read_text(rel, 200) or "").splitlines()[0] if project.read_text(rel, 200) else ""))
        input_mode, known = _input_for(arguments or "")
        if arguments is not None:
            confidence, source = "high", f"README documents running ./{rel}"
        elif _rank(rel) < len(ENTRY_NAMES):
            confidence, source = "medium", f"{rel} is an executable entry point"
        else:
            confidence, source = "low", f"{rel} is executable"
        finding.entrypoints.append(
            Entrypoint(argv=[f"./{rel}"], label=f"./{rel}", confidence=confidence, source=source, input=input_mode, input_known=known, runtime_kind="cli")
        )
        if not finding.evidence:
            finding.evidence.append(f"Executable script: {rel}" + (" (documented in the README)" if arguments is not None else ""))
        if not interpreted and confidence == "high":
            finding.evidence.append("It is a compiled program")
    if not finding.name:
        finding.name = project.name
    return finding
