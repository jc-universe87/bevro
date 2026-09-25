"""Which paths a browser-facing service passes on to another service in the same application.

A Compose application often publishes one port, for its website, and keeps
the service that does the work on the private network behind it: the web
server answers `/` with the page, and passes `/api/...` on. The only address
anything outside can use is then the website's, plus that path.

This reads the web server's own configuration to find those paths - and only
the configuration that the service really uses: files its Dockerfile copies
into the web server's config directory, files mounted there, and failing
those the conventional names in its build directory. Two web servers, and
only their simplest, commonest forms:

  nginx   location /api/ { proxy_pass http://backend:8000; }
          (and `^~` / `=` locations, and `upstream` blocks naming one server)
  Caddy   reverse_proxy /api/* backend:8000
          handle /api/* { reverse_proxy backend:8000 }   (and handle_path)

A route counts only when it passes to another service of the same Compose
application: that is what makes it evidence of a part behind the website,
rather than a proxy to somewhere else. Regular-expression locations,
variables, and anything else clever are skipped, not guessed at.

What comes out is a public path prefix and the service behind it. The
service's name is internal and stays server-side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.connect.inspect import Project

MAX_FILES = 8
MAX_ROUTES = 3
CONFIG_DIRS = ("/etc/nginx/", "/etc/caddy/")
CONVENTIONAL = ("nginx.conf", "default.conf", "nginx/default.conf", "nginx/nginx.conf", "conf.d/default.conf", "Caddyfile")

_COPY = re.compile(r"^\s*(?:COPY|ADD)\s+(?!--from)(?:--\S+\s+)*(.+)$", re.I | re.M)
_LOCATION = re.compile(r"\blocation\s+(?:(=|\^~)\s+)?(/[^\s{;]*)\s*\{")
_PROXY_PASS = re.compile(r"\bproxy_pass\s+https?://([A-Za-z0-9_.-]+)(?::(\d{1,5}))?(/[^\s;]*)?\s*;")
_UPSTREAM = re.compile(r"\bupstream\s+([A-Za-z0-9_.-]+)\s*\{([^{}]*)\}")
_UPSTREAM_SERVER = re.compile(r"\bserver\s+([A-Za-z0-9_.-]+)(?::(\d{1,5}))?")
_CADDY_INLINE = re.compile(r"^\s*reverse_proxy\s+(/\S*)\s+(?:https?://)?([A-Za-z0-9_.-]+):(\d{1,5})\s*$", re.M)
_CADDY_HANDLE = re.compile(r"^\s*(handle_path|handle)\s+(/\S*)\s*\{", re.M)
_CADDY_BLOCK_PROXY = re.compile(r"^\s*reverse_proxy\s+(?:https?://)?([A-Za-z0-9_.-]+):(\d{1,5})\s*$", re.M)


@dataclass(frozen=True)
class ProxyRoute:
    """One path the website passes on. `upstream` is a Compose service name: server-side only."""

    prefix: str  # "/api": no trailing slash, never "/"
    upstream: str
    port: int | None
    server: str  # "nginx" | "caddy"


def _context(svc: dict[str, Any]) -> str | None:
    build = svc.get("build")
    context = build if isinstance(build, str) else (build.get("context") if isinstance(build, dict) else None)
    if not isinstance(context, str):
        return None
    rel = str(PurePosixPath(context.strip() or "."))
    if rel.startswith(("/", "..")) or "/../" in f"/{rel}/":
        return None
    return rel


def _join(base: str, rel: str) -> str | None:
    rel = rel.strip().strip("\"'")
    if not rel or rel.startswith(("/", "http:", "https:", "$")) or ".." in PurePosixPath(rel).parts:
        return None
    return str(PurePosixPath(base) / rel) if base not in ("", ".") else rel


def config_files(project: Project, svc: dict[str, Any]) -> list[str]:
    """The web-server configuration files this service is built or run with, inside the project."""
    found: list[str] = []

    def add(rel: str | None) -> None:
        if rel is None or rel in found or len(found) >= MAX_FILES:
            return
        if project.is_file(rel):
            found.append(rel)
        elif project.is_dir(rel):
            for name in project.listdir(rel):
                if name.endswith(".conf") or name == "Caddyfile":
                    add(f"{rel}/{name}")

    context = _context(svc)
    build = svc.get("build")
    dockerfile = build.get("dockerfile", "Dockerfile") if isinstance(build, dict) else "Dockerfile"
    if context is not None:
        text = project.read_text(_join(context, str(dockerfile)) or "", 32_000) or ""
        for m in _COPY.finditer(text):
            words = m.group(1).split()
            if len(words) >= 2 and words[-1].startswith(CONFIG_DIRS):
                for source in words[:-1]:
                    add(_join(context, source))
    for volume in svc.get("volumes") or []:
        source = target = None
        if isinstance(volume, str) and ":" in volume:
            source, target = volume.split(":")[0], volume.split(":")[1]
        elif isinstance(volume, dict):
            source, target = volume.get("source"), volume.get("target")
        if isinstance(source, str) and isinstance(target, str) and target.startswith(CONFIG_DIRS):
            add(_join(".", source.removeprefix("./")))
    if not found and context is not None:
        for name in CONVENTIONAL:
            add(_join(context, name))
    return found


def _without_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _block(text: str, start: int) -> str:
    """The body of the `{` block opening just before `start`."""
    depth = 1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return ""


def _prefix(path: str) -> str | None:
    prefix = path.rstrip("*").rstrip("/")
    if not prefix or not prefix.startswith("/") or any(c in prefix for c in "$*?()[]^~\\"):
        return None
    return prefix


def nginx_routes(text: str) -> list[tuple[str, str, int | None]]:
    """(prefix, upstream host, upstream port) for each simple proxied location."""
    text = _without_comments(text)
    upstreams: dict[str, tuple[str, int | None]] = {}
    for m in _UPSTREAM.finditer(text):
        server = _UPSTREAM_SERVER.search(m.group(2))
        if server:
            upstreams[m.group(1)] = (server.group(1), int(server.group(2)) if server.group(2) else None)
    out: list[tuple[str, str, int | None]] = []
    for m in _LOCATION.finditer(text):
        prefix = _prefix(m.group(2))
        if prefix is None:
            continue
        passed = _PROXY_PASS.search(_block(text, m.end()))
        if passed is None:
            continue
        host, port = passed.group(1), int(passed.group(2)) if passed.group(2) else None
        if host in upstreams:
            host, port = upstreams[host]
        out.append((prefix, host, port))
    return out


def caddy_routes(text: str) -> list[tuple[str, str, int | None]]:
    text = _without_comments(text)
    out: list[tuple[str, str, int | None]] = []
    for m in _CADDY_INLINE.finditer(text):
        prefix = _prefix(m.group(1))
        if prefix is not None:
            out.append((prefix, m.group(2), int(m.group(3))))
    for handle in _CADDY_HANDLE.finditer(text):
        prefix = _prefix(handle.group(2))
        proxied = _CADDY_BLOCK_PROXY.search(_block(text, handle.end()))
        if prefix is not None and proxied is not None:
            out.append((prefix, proxied.group(1), int(proxied.group(2))))
    return out


def proxy_routes(project: Project, svc: dict[str, Any], services: set[str], own_name: str) -> list[ProxyRoute]:
    """The paths this service passes on to another service of the same application."""
    routes: list[ProxyRoute] = []
    for rel in config_files(project, svc):
        text = project.read_text(rel, 64_000) or ""
        server = "caddy" if PurePosixPath(rel).name == "Caddyfile" else "nginx"
        found = caddy_routes(text) if server == "caddy" else nginx_routes(text)
        for prefix, host, port in found:
            if host not in services or host == own_name:
                continue  # somewhere else entirely, or itself
            route = ProxyRoute(prefix=prefix, upstream=host, port=port, server=server)
            if all(r.prefix != route.prefix for r in routes):
                routes.append(route)
    return routes[:MAX_ROUTES]
