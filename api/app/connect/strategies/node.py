"""Static inspection of a Node project. package.json is read; nothing is installed or run."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from app.connect.inspect import Project
from app.connect.strategies.project import SECRET_BY_DEPENDENCY, Entrypoint, Finding

HTTP_FRAMEWORKS = ("express", "fastify", "koa", "hono", "@nestjs/core", "next", "nuxt")
_PORT = re.compile(r"^\s*PORT\s*=\s*(\d{2,5})", re.M)


def _runs_by_itself(project: Project, rel: str) -> bool:
    """Does this file claim to be a program rather than something to import?

    An executable bit or a shebang is the author saying "run me". Anything
    else is read as a module, which is what `main` usually means.
    """
    if project.is_executable(rel):
        return True
    head = project.read_text(rel, 200) or ""
    return head.startswith("#!")


def inspect_node(project: Project) -> Finding | None:
    text = project.read_text("package.json", 64_000)
    if text is None:
        return None
    try:
        pkg = json.loads(text)
    except ValueError:
        return Finding(kind="node", name=project.name, warnings=["package.json could not be read."])
    if not isinstance(pkg, dict):
        return None

    finding = Finding(kind="node")
    finding.name = str(pkg.get("name") or project.name).split("/")[-1]
    version = f" {pkg['version']}" if pkg.get("version") else ""
    finding.evidence.append(f"package.json declares {finding.name}{version}")
    if pkg.get("description"):
        finding.description = str(pkg["description"])
    deps = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(key), dict):
            deps.update(pkg[key])
    finding.dependencies = sorted(deps)
    installed = project.is_dir("node_modules")
    if not installed:
        finding.warnings.append("Dependencies are not installed (no node_modules). Run npm install yourself before connecting.")

    if "dotenv" in deps or "@dotenvx/dotenvx" in deps:
        finding.loads_dotenv = True
    is_mcp = "@modelcontextprotocol/sdk" in deps
    mechanism = "mcp" if is_mcp else "command"
    if is_mcp:
        finding.frameworks.add("mcp")
        finding.evidence.append("Depends on @modelcontextprotocol/sdk: an MCP server")
    for fw in HTTP_FRAMEWORKS:
        if fw in deps:
            finding.frameworks.add(fw)

    bins = pkg.get("bin")
    if isinstance(bins, str):
        bins = {finding.name: bins}
    if isinstance(bins, dict):
        for bin_name, rel in list(bins.items())[:5]:
            rel = str(rel).lstrip("./")
            if project.is_file(rel):
                finding.entrypoints.append(Entrypoint(argv=["node", rel], label=f"node {rel}", confidence="high", source=f"package.json declares the command '{bin_name}'", mechanism=mechanism))
    # "main" is where `require()` lands, not a program to run. A library whose
    # main file only assigns exports does nothing when executed, so it is not a
    # runtime - it is a module a bridge can call. Only take main as runnable
    # when the project says it is: an executable file or a shebang, an MCP
    # server, or a web framework whose main file starts it.
    main = pkg.get("main") or pkg.get("module")
    if isinstance(main, str):
        rel = main.lstrip("./")
        already = any(e.argv[-1] == rel for e in finding.entrypoints)
        if project.is_file(rel) and not already:
            runnable = _runs_by_itself(project, rel) or is_mcp or bool(finding.frameworks & set(HTTP_FRAMEWORKS))
            if runnable:
                finding.entrypoints.append(
                    Entrypoint(argv=["node", rel], label=f"node {rel}", confidence="medium", source="package.json names its main file", mechanism=mechanism)
                )
            else:
                finding.evidence.append(f"package.json names {rel} as what `require()` returns: a module, not a program")
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    start = scripts.get("start")
    if isinstance(start, str) and start.strip():
        try:
            words = shlex.split(start)
        except ValueError:
            words = []
        if words and words[0] in ("node", "tsx", "ts-node") and len(words) >= 2 and project.is_file(words[-1].lstrip("./")):
            if not any(e.argv[-1] == words[-1].lstrip("./") for e in finding.entrypoints):
                finding.entrypoints.append(Entrypoint(argv=[words[0], words[-1].lstrip("./")], label=f"{words[0]} {words[-1]}", confidence="medium", source="package.json's start script", mechanism=mechanism))
        elif words:
            finding.entrypoints.append(Entrypoint(argv=["npm", "start", "--"], label="npm start", confidence="low", source="package.json has a start script", mechanism=mechanism))
    if scripts:
        finding.evidence.append("Scripts: " + ", ".join(sorted(scripts)[:8]))

    if any(fw in finding.frameworks for fw in HTTP_FRAMEWORKS):
        port = 3000
        for name in project.listdir():
            if name.startswith(".env.") and name.endswith(("example", "sample", "template")):
                m = _PORT.search(project.read_text(name, 8_000) or "")
                if m:
                    port = int(m.group(1))
        finding.services.append({"port": port, "source": "package.json depends on a web framework"})

    for dep in deps:
        secret = SECRET_BY_DEPENDENCY.get(dep) or SECRET_BY_DEPENDENCY.get(str(dep).lower())
        if secret and secret not in finding.env_secret_names:
            finding.env_secret_names.append(secret)
    for key in project.env_example_keys():
        if key.endswith(("_KEY", "_TOKEN", "_SECRET")) and key not in finding.env_secret_names:
            finding.env_secret_names.append(key)
    return finding
