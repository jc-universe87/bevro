"""Docker metadata: compose services and Dockerfiles, read only. No container is started."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

import yaml

from app.connect.inspect import Project
from app.connect.strategies.project import Finding

COMPOSE_FILES = ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")
INFRA_IMAGES = ("postgres", "mysql", "mariadb", "redis", "rabbitmq", "mongo", "memcached", "elasticsearch", "minio", "nginx", "traefik", "caddy")
APP_NAMES = ("app", "api", "agent", "server", "web", "backend", "service")
_EXPOSE = re.compile(r"^\s*EXPOSE\s+(\d{2,5})", re.M)
_HEALTH_URL = re.compile(r"https?://[^/\s\"']+(/[\w./-]*)")
_ENTRYPOINT = re.compile(r"^\s*ENTRYPOINT\s+(.+)$", re.M | re.I)


def _argv(value: Any) -> list[str] | None:
    """An entrypoint as Compose writes one: a list, or a string split like a shell would."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        try:
            return shlex.split(value)
        except ValueError:
            return None
    return None


def _dockerfile_entrypoint(project: Project, svc: dict[str, Any]) -> list[str] | None:
    """The ENTRYPOINT of an image built from this project's own Dockerfile.

    Only the project's top-level Dockerfile, built from the project itself,
    is read; anything else is somebody else's image. The shell form runs
    through /bin/sh, and is recorded as exactly that.
    """
    build = svc.get("build")
    context = build if isinstance(build, str) else (build.get("context") if isinstance(build, dict) else None)
    dockerfile = build.get("dockerfile", "Dockerfile") if isinstance(build, dict) else "Dockerfile"
    if str(context or "").rstrip("/") not in (".", "") or dockerfile != "Dockerfile":
        return None
    match = None
    for match in _ENTRYPOINT.finditer(project.read_text("Dockerfile", 16_000) or ""):
        pass  # the last ENTRYPOINT is the one that counts
    if match is None:
        return None
    text = match.group(1).strip()
    if text.startswith("["):
        try:
            value = json.loads(text)
        except ValueError:
            return None
        return [str(v) for v in value] if isinstance(value, list) else None
    return ["/bin/sh", "-c", text]


def _containers(project: Project, compose_name: str, services: dict[str, Any]) -> list[dict[str, Any]]:
    """Every service built or run as an application, as a one-off could run it."""
    out: list[dict[str, Any]] = []
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        image = str(svc.get("image") or "")
        if any(image.startswith(i) or f"/{i}" in image for i in INFRA_IMAGES) and not svc.get("build"):
            continue
        entrypoint = _argv(svc.get("entrypoint"))
        if entrypoint is None and svc.get("build"):
            entrypoint = _dockerfile_entrypoint(project, svc)
        out.append({"file": compose_name, "service": str(name), "entrypoint": entrypoint, "built_here": bool(svc.get("build")), "ports": bool(svc.get("ports")), "svc": svc})
    return out[:6]


def _host_port(entry: Any) -> int | None:
    if isinstance(entry, int):
        return entry
    if isinstance(entry, str):
        parts = entry.split(":")
        try:
            return int(parts[-2]) if len(parts) >= 2 else int(parts[0].split("/")[0])
        except ValueError:
            return None
    if isinstance(entry, dict):
        published = entry.get("published") or entry.get("target")
        try:
            return int(published)
        except (TypeError, ValueError):
            return None
    return None


def inspect_docker(project: Project) -> Finding | None:
    compose_name = next((n for n in COMPOSE_FILES if project.is_file(n)), None)
    has_dockerfile = project.is_file("Dockerfile")
    if not compose_name and not has_dockerfile:
        return None
    finding = Finding(kind="docker")
    if has_dockerfile:
        finding.evidence.append("Dockerfile present")
        m = _EXPOSE.search(project.read_text("Dockerfile", 16_000) or "")
        if m and not compose_name:
            finding.services.append({"port": int(m.group(1)), "source": f"Dockerfile exposes port {m.group(1)}", "start": "docker"})
    if not compose_name:
        return finding
    try:
        data = yaml.safe_load(project.read_text(compose_name) or "") or {}
    except yaml.YAMLError:
        finding.warnings.append(f"{compose_name} could not be read.")
        return finding
    services = data.get("services") if isinstance(data, dict) and isinstance(data.get("services"), dict) else {}
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        image = str(svc.get("image") or "")
        if any(image.startswith(i) or f"/{i}" in image for i in INFRA_IMAGES) and not svc.get("build"):
            continue
        ports = [p for p in (_host_port(e) for e in (svc.get("ports") or [])) if p]
        if not ports:
            continue
        score = 0
        if svc.get("build"):
            score += 2
        if any(a in str(name).lower() for a in APP_NAMES):
            score += 1
        if svc.get("healthcheck"):
            score += 1
        candidates.append((score, str(name), {"port": ports[0], "svc": svc}))
    candidates.sort(key=lambda c: -c[0])
    for _score, name, info in candidates[:3]:
        svc = info["svc"]
        service: dict[str, Any] = {"port": info["port"], "source": f"{compose_name}: service '{name}' publishes port {info['port']}", "start": "compose", "service": name, "svc": svc}
        health = svc.get("healthcheck") or {}
        test = health.get("test") if isinstance(health, dict) else None
        test_text = " ".join(test) if isinstance(test, list) else str(test or "")
        m = _HEALTH_URL.search(test_text)
        if m and m.group(1).startswith("/"):
            service["health_path"] = m.group(1)
        if svc.get("depends_on"):
            deps = svc["depends_on"]
            service["depends_on"] = list(deps.keys()) if isinstance(deps, dict) else [str(d) for d in deps]
        finding.services.append(service)
        finding.evidence.append(service["source"])
    finding.containers = _containers(project, compose_name, services)
    if services and not candidates:
        finding.evidence.append(f"{compose_name} defines {len(services)} service(s), none publishing a port")
    return finding
