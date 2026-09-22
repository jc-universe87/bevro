"""Static inspection of a Python project. Nothing is imported or executed."""

from __future__ import annotations

import re
import tomllib
from typing import Any

from app.connect.inspect import Project
from app.connect.strategies.project import INPUT_FLAGS, INPUT_POSITIONALS, SECRET_BY_DEPENDENCY, Entrypoint, Finding

MAX_MODULES_SCANNED = 40
MODULE_PREFERENCE = ("agent", "main", "cli", "app", "run", "server", "__init__")
TOP_LEVEL_SCRIPTS = ("main.py", "app.py", "agent.py", "cli.py", "run.py", "server.py")

_MAIN_GUARD = re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]")
_ARGPARSE = re.compile(r"argparse\.ArgumentParser\(|@click\.command|typer\.Typer\(|@app\.command")
_FASTAPI = re.compile(r"\bFastAPI\(")
_FLASK = re.compile(r"\bFlask\(")
_MCP = re.compile(r"\bFastMCP\(|from\s+mcp(\.|\s)|import\s+mcp\b")
_ADD_ARG = re.compile(r"add_argument\(\s*['\"]([^'\"]+)['\"]")
_README_MODULE = re.compile(r"python[0-9.]*\s+-m\s+([A-Za-z_][\w.]*)")
_STDIN = re.compile(r"sys\.stdin|input\(\)")
_DEP_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _dep_names(specs: list[Any]) -> list[str]:
    names: list[str] = []
    for spec in specs:
        if not isinstance(spec, str):
            continue
        m = _DEP_NAME.match(spec)
        if m:
            names.append(m.group(1).lower())
    return names


def _requirements(project: Project) -> list[str]:
    text = project.read_text("requirements.txt", 16_000)
    if not text:
        return []
    names: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _DEP_NAME.match(line)
        if m:
            names.append(m.group(1).lower())
    return names


def _interpreter(project: Project) -> tuple[str, str | None]:
    """(argv0, evidence). The project's own virtual environment when it has one."""
    for venv in (".venv", "venv"):
        program = project.venv_program(f"{venv}/bin/python")
        if program:
            return program, f"Uses the project's own virtual environment ({venv})"
    return "python3", None


def _packages(project: Project, where: list[str]) -> list[tuple[str, str]]:
    """[(package name, relative dir)] for importable packages."""
    found: list[tuple[str, str]] = []
    for base in where:
        for entry in project.listdir(base):
            rel = f"{base}/{entry}" if base != "." else entry
            if project.is_dir(rel) and project.is_file(f"{rel}/__init__.py") and not entry.endswith(".egg-info"):
                found.append((entry, rel))
    return found


def _input_from_source(text: str) -> tuple[dict[str, Any], bool]:
    flags = _ADD_ARG.findall(text)
    for flag in INPUT_FLAGS:
        if flag in flags:
            return {"mode": "flag", "flag": flag}, True
    for pos in INPUT_POSITIONALS:
        if pos in flags:
            return {"mode": "argument"}, True
    if _STDIN.search(text):
        return {"mode": "stdin"}, True
    return {"mode": "argument"}, False


def inspect_python(project: Project) -> Finding | None:
    has_pyproject = project.is_file("pyproject.toml")
    has_setup = project.is_file("setup.py") or project.is_file("setup.cfg")
    has_requirements = project.is_file("requirements.txt")
    top_level_py = [n for n in project.listdir() if n.endswith(".py")]
    if not (has_pyproject or has_setup or has_requirements or top_level_py):
        return None

    finding = Finding(kind="python")
    meta: dict[str, Any] = {}
    scripts: dict[str, str] = {}
    where = ["."]
    if has_pyproject:
        try:
            data = tomllib.loads(project.read_text("pyproject.toml") or "")
        except (tomllib.TOMLDecodeError, ValueError):
            data = {}
            finding.warnings.append("pyproject.toml could not be read.")
        proj = data.get("project") if isinstance(data.get("project"), dict) else {}
        poetry = (data.get("tool") or {}).get("poetry") if isinstance(data.get("tool"), dict) else {}
        poetry = poetry if isinstance(poetry, dict) else {}
        meta = proj or poetry
        if meta.get("name"):
            finding.name = str(meta["name"])
            version = f" {meta['version']}" if meta.get("version") else ""
            finding.evidence.append(f"pyproject.toml declares {meta['name']}{version}")
        if meta.get("description"):
            finding.description = str(meta["description"])
        scripts = {k: str(v) for k, v in (meta.get("scripts") or {}).items()} if isinstance(meta.get("scripts"), dict) else {}
        finding.dependencies = _dep_names(list(meta.get("dependencies") or []) if isinstance(meta.get("dependencies"), list) else list((meta.get("dependencies") or {}).keys()))
        # Any shape may appear in a file Bevro did not write: packages can be a
        # table with `find`, or a plain list of package names.
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        setuptools = tool.get("setuptools") if isinstance(tool.get("setuptools"), dict) else {}
        packages = setuptools.get("packages") if isinstance(setuptools, dict) else None
        find = packages.get("find") if isinstance(packages, dict) else None
        if isinstance(find, dict) and isinstance(find.get("where"), list):
            where = [str(w) for w in find["where"]] + ["."]
        elif project.is_dir("src"):
            where = ["src", "."]
    elif project.is_dir("src"):
        where = ["src", "."]
    if has_setup:
        finding.evidence.append("setup.py / setup.cfg present")
    if has_requirements:
        finding.dependencies += [d for d in _requirements(project) if d not in finding.dependencies]
        finding.evidence.append("requirements.txt lists dependencies")

    interpreter, venv_note = _interpreter(project)
    if venv_note:
        finding.evidence.append(venv_note)
    else:
        finding.warnings.append("No virtual environment found in the project; it will run with the machine's python3.")

    packages = _packages(project, where)
    src_base = next((rel.rsplit("/", 1)[0] for _pkg, rel in packages if "/" in rel), None)
    if src_base:
        # An src/ layout: make the package importable even if it was never pip-installed.
        finding.env["PYTHONPATH"] = str(project.root / src_base)
    readme = project.read_text("README.md", 64_000) or ""
    documented_modules = set(_README_MODULE.findall(readme))

    # 1. Declared console scripts.
    for script_name, ref in scripts.items():
        module = ref.split(":", 1)[0]
        venv_bin = next((p for p in (project.venv_program(f"{venv}/bin/{script_name}") for venv in (".venv", "venv")) if p), None)
        text = _module_text(project, packages, module) or ""
        inp, known = _input_from_source(text)
        if venv_bin:
            finding.entrypoints.append(Entrypoint(argv=[venv_bin], label=script_name, confidence="high", source=f"pyproject.toml declares the command '{script_name}'", input=inp, input_known=known))
        else:
            finding.entrypoints.append(Entrypoint(argv=[interpreter, "-m", module], label=f"python -m {module}", confidence="medium", source=f"pyproject.toml declares the command '{script_name}' ({module})", input=inp, input_known=known))

    # 2. Packages: __main__.py, then modules with a main guard.
    for pkg, rel in packages:
        if project.is_file(f"{rel}/__main__.py"):
            text = project.read_text(f"{rel}/__main__.py") or ""
            inp, known = _input_from_source(text)
            finding.entrypoints.append(Entrypoint(argv=[interpreter, "-m", pkg], label=f"python -m {pkg}", confidence="high", source=f"{pkg}/__main__.py makes the package runnable", input=inp, input_known=known))
        modules = [n[:-3] for n in project.listdir(rel) if n.endswith(".py") and n != "__main__.py"][:MAX_MODULES_SCANNED]
        modules.sort(key=lambda n: MODULE_PREFERENCE.index(n) if n in MODULE_PREFERENCE else len(MODULE_PREFERENCE))
        for mod in modules:
            text = project.read_text(f"{rel}/{mod}.py") or ""
            _note_frameworks(finding, text)
            if not _MAIN_GUARD.search(text):
                continue
            dotted = f"{pkg}.{mod}"
            inp, known = _input_from_source(text)
            documented = dotted in documented_modules
            cli = bool(_ARGPARSE.search(text))
            if _MCP.search(text):
                finding.entrypoints.append(Entrypoint(argv=[interpreter, "-m", dotted], label=f"python -m {dotted}", confidence="medium", source=f"{mod}.py looks like an MCP server", mechanism="mcp"))
                continue
            if documented:
                conf, source = "high", f"README documents python -m {dotted}"
            elif cli and mod in MODULE_PREFERENCE:
                conf, source = "medium", f"{mod}.py has a command-line interface"
            elif cli:
                conf, source = "low", f"{mod}.py can be run as a program"
            else:
                conf, source = "low", f"{mod}.py can be run as a program"
            finding.entrypoints.append(Entrypoint(argv=[interpreter, "-m", dotted], label=f"python -m {dotted}", confidence=conf, source=source, input=inp, input_known=known))

    # 3. Conventional top-level scripts.
    for script in TOP_LEVEL_SCRIPTS:
        if script in top_level_py:
            text = project.read_text(script) or ""
            _note_frameworks(finding, text)
            if _MAIN_GUARD.search(text) or script in ("main.py", "cli.py", "run.py"):
                inp, known = _input_from_source(text)
                mech = "mcp" if _MCP.search(text) else "command"
                finding.entrypoints.append(Entrypoint(argv=[interpreter, script], label=f"python {script}", confidence="medium" if _ARGPARSE.search(text) else "low", source=f"{script} at the project root", input=inp, input_known=known, mechanism=mech))

    if any(d in ("python-dotenv", "dotenv") for d in finding.dependencies):
        finding.loads_dotenv = True
    for dep in finding.dependencies:
        if dep in ("fastapi", "flask", "starlette", "django"):
            finding.frameworks.add(dep)
        if dep in ("mcp", "fastmcp"):
            finding.frameworks.add("mcp")
        secret = SECRET_BY_DEPENDENCY.get(dep)
        if secret and secret not in finding.env_secret_names:
            finding.env_secret_names.append(secret)
    for key in project.env_example_keys():
        if key.endswith(("_KEY", "_TOKEN", "_SECRET")) and key not in finding.env_secret_names:
            finding.env_secret_names.append(key)
    if not finding.name:
        finding.name = project.name
    return finding


_DOTENV = re.compile(r"\bload_dotenv\s*\(|from\s+dotenv\s+import|import\s+dotenv\b")


def _note_frameworks(finding: Finding, text: str) -> None:
    if _DOTENV.search(text):
        finding.loads_dotenv = True
    if _FASTAPI.search(text):
        finding.frameworks.add("fastapi")
    if _FLASK.search(text):
        finding.frameworks.add("flask")
    if _MCP.search(text):
        finding.frameworks.add("mcp")


def _module_text(project: Project, packages: list[tuple[str, str]], dotted: str) -> str | None:
    parts = dotted.split(".")
    for pkg, rel in packages:
        if parts[0] == pkg:
            path = "/".join([rel, *parts[1:]]) + ".py"
            if project.is_file(path):
                return project.read_text(path)
            init = "/".join([rel, *parts[1:], "__init__.py"])
            if project.is_file(init):
                return project.read_text(init)
    return None
