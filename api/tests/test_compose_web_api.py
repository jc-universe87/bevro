"""A Compose application's website is not its way in; what is behind it may be.

The shape these fixtures model is common: one published port for a website,
a web server that passes one path on to a service kept on the private
network, and - sometimes - a machine description behind that path. Bevro
reads the web server's own configuration to know where to look, looks only
there, and claims a way to send work only when something describes itself.

Every service here is a small local HTTP server; every project is synthetic.
"""

from __future__ import annotations

import json
import socket
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.connect.compose_ports import published_port, resolve_port
from app.connect.inspect import Project
from app.connect.proxies import caddy_routes, nginx_routes, proxy_routes
from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext
from app.connect.strategies.docker import inspect_docker
from app.connect.strategies.local import LocalProjectStrategy
from app.connect.targets import classify_target
from tests.operational_fixtures import READ_ONLY_API, SPA_HTML

SECRET = "fixture-token-must-never-appear"
UPSTREAM = "private-worker-svc"  # the internal service name: server-side only


# --------------------------------------------------------------------------- a local website

class FakeSite:
    """A website on a real port: a page for people everywhere, and, under
    `prefix`, whatever the service behind it answers."""

    def __init__(self, *, prefix: str | None = None, health: bool = True, descriptor: dict[str, Any] | None = None, descriptor_path: str | None = None, root_descriptor: dict[str, Any] | None = None, page: bool = True) -> None:
        self.prefix, self.health, self.descriptor, self.root_descriptor, self.page = prefix, health, descriptor, root_descriptor, page
        self.descriptor_path = descriptor_path or (f"{prefix}/openapi.json" if prefix else "/openapi.json")
        self.seen: list[tuple[str, str]] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, ctype: str, body: str) -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                site.seen.append(("GET", self.path))
                path = self.path.split("?", 1)[0]
                if site.descriptor is not None and path == site.descriptor_path:
                    return self._send(200, "application/json", json.dumps(site.descriptor))
                if site.root_descriptor is not None and path == "/openapi.json":
                    return self._send(200, "application/json", json.dumps(site.root_descriptor))
                if site.prefix and (path == site.prefix or path.startswith(site.prefix + "/")):
                    if site.health and path == f"{site.prefix}/health":
                        return self._send(200, "application/json", json.dumps({"status": "ok"}))
                    return self._send(404, "application/json", json.dumps({"detail": "Not Found"}))
                if site.page:
                    return self._send(200, "text/html; charset=utf-8", SPA_HTML)
                return self._send(404, "application/json", json.dumps({"detail": "Not Found"}))

            def do_POST(self) -> None:  # noqa: N802
                site.seen.append(("POST", self.path))
                self._send(405, "application/json", json.dumps({"detail": "Method Not Allowed"}))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sites():
    started: list[FakeSite] = []

    def start(**kw: Any) -> FakeSite:
        s = FakeSite(**kw)
        started.append(s)
        return s

    yield start
    for s in started:
        s.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stripped(spec: dict[str, Any], prefix: str) -> dict[str, Any]:
    """The same API as the service behind a prefix-stripping proxy sees itself."""
    return {**spec, "paths": {p.removeprefix(prefix): v for p, v in spec["paths"].items()}}


def discover(project: Path):
    return ConnectionDiscoveryService([LocalProjectStrategy()], use_assist=False).discover(classify_target(str(project)), DiscoveryContext(roots=[project.parent]))


# --------------------------------------------------------------------------- synthetic projects

def make_web_app(root: Path, *, port_line: str, prefix: str | None = "/api", web_server: str = "nginx", stock_image: bool = False, env: str | None = None, upstream: str = UPSTREAM, strip: bool = False, cli: bool = False, name: str = "fixture-webapp") -> Path:
    """A website service in front of a private service, the way such apps are laid out."""
    project = root / name
    (project / "web").mkdir(parents=True)
    (project / "worker").mkdir()
    web_service = (
        f"""
          web:
            image: nginx:alpine
            ports:
              - "{port_line}"
            volumes:
              - ./web/site.conf:/etc/nginx/conf.d/default.conf:ro
        """
        if stock_image
        else f"""
          web:
            build: ./web
            ports:
              - "{port_line}"
            depends_on:
              - {upstream}
        """
    )
    (project / "docker-compose.yml").write_text(
        textwrap.dedent(
            f"""
            services:
              db:
                image: postgres:16-alpine
              {upstream}:
                build: ./worker
                env_file: .env
            """
        ).rstrip()
        + textwrap.indent(textwrap.dedent(web_service), "  ")
        + "\n",
        encoding="utf-8",
    )
    if web_server == "nginx":
        conf = "site.conf" if stock_image else "nginx.conf"
        location = (
            f"""
            location {prefix}/ {{
                proxy_pass http://{upstream}:8000{'/' if strip else ''};
                proxy_set_header Host $host;
            }}
            """
            if prefix
            else ""
        )
        (project / "web" / conf).write_text(
            textwrap.dedent(
                """
                server {
                    listen 80;
                    root /usr/share/nginx/html;
                """
            )
            + textwrap.indent(textwrap.dedent(location), "    ")
            + "    location / { try_files $uri /index.html; }\n}\n",
            encoding="utf-8",
        )
        if not stock_image:
            (project / "web" / "Dockerfile").write_text("FROM nginx:alpine\nCOPY nginx.conf /etc/nginx/conf.d/default.conf\n", encoding="utf-8")
    else:
        (project / "web" / "Caddyfile").write_text(f":80 {{\n    reverse_proxy {prefix}/* {upstream}:8000\n    file_server\n}}\n", encoding="utf-8")
        (project / "web" / "Dockerfile").write_text("FROM caddy:2\nCOPY Caddyfile /etc/caddy/Caddyfile\n", encoding="utf-8")
    (project / "worker" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (project / ".env").write_text((env or "") + f"UPSTREAM_TOKEN={SECRET}\n", encoding="utf-8")
    (project / "README.md").write_text("# Fixture Webapp\n\nKeeps notes about widgets and answers questions about them.\n", encoding="utf-8")
    if cli:
        (project / "pyproject.toml").write_text('[project]\nname = "fixture-webapp"\ndescription = "Fixture Webapp"\n\n[project.scripts]\nfixture-webapp = "webapp.cli:main"\n', encoding="utf-8")
        (project / "webapp").mkdir()
        (project / "webapp" / "__init__.py").write_text("", encoding="utf-8")
        (project / "webapp" / "cli.py").write_text('import argparse\n\ndef main():\n    p = argparse.ArgumentParser()\n    p.add_argument("--prompt")\n    print(p.parse_args().prompt)\n', encoding="utf-8")
    return project


def assert_private(draft) -> None:
    dump, public = str(draft.model_dump()), str(draft.public())
    assert SECRET not in dump and SECRET not in public
    assert UPSTREAM not in public  # the internal service's name never reaches the browser


# --------------------------------------------------------------------------- ports, as Compose reads them

def lookup(values: dict[str, str]):
    return lambda name: ("port", int(values[name])) if values.get(name, "").isdigit() else (("empty", None) if values.get(name) == "" else (("other", None) if name in values else ("unset", None)))


@pytest.mark.parametrize(
    ("entry", "values", "expected"),
    [
        ("6400:80", {}, 6400),  # A: short
        ("127.0.0.1:6400:80", {}, 6400),  # IPv4 binding
        ("${PORT:-6400}:80", {}, 6400),  # B: default when unset
        ("127.0.0.1:${PORT:-6400}:80", {}, 6400),
        ("127.0.0.1:${PORT:-6400}:80", {"PORT": "6500"}, 6500),  # C: set
        ("${PORT:-6400}:80", {"PORT": ""}, 6400),  # ":-" also covers empty
        ("${PORT-6400}:80", {"PORT": ""}, None),  # "-" does not
        ("${HOST_PORT}:8000", {}, None),  # missing, no default: not known
        ("127.0.0.1:${HOST_PORT}:8000", {"HOST_PORT": "9100"}, 9100),
        ("$HOST_PORT:8000", {"HOST_PORT": "9100"}, 9100),
        ("${PORT:?set it}:80", {}, None),  # required and missing
        ("${PORT:-6400:80", {}, None),  # malformed
        ("${PORT:-notaport}:80", {}, None),
        ("${PORT:-6400}:80", {"PORT": "abc"}, None),  # set to something that is not a port
        ("127.0.0.1::80", {}, None),  # Docker picks: unknown
        ("8000-8003:8000-8003", {}, None),  # a range: not guessed
        ("[::1]:8080:80", {}, 8080),
        ("8080:80/tcp", {}, 8080),
        ({"target": 80, "published": "${PORT:-7000}"}, {}, 7000),  # long syntax
        ({"target": 80, "published": 7001, "host_ip": "127.0.0.1"}, {}, 7001),
    ],
)
def test_published_port_reads_compose_expressions(entry, values, expected):
    assert published_port(entry, lookup(values)) == expected


def test_quoted_and_several_ports_in_a_real_compose_file(tmp_path):
    project = tmp_path / "multi"
    project.mkdir()
    (project / "docker-compose.yml").write_text(
        'services:\n  app:\n    build: .\n    ports:\n      - "127.0.0.1:${APP_PORT:-6400}:80"\n      - \'${OTHER:-6401}:81\'\n      - 6402:82\n',
        encoding="utf-8",
    )
    (project / ".env").write_text(f"APP_PORT=6555\nAPI_SECRET={SECRET}\n", encoding="utf-8")
    finding = inspect_docker(Project(project))
    assert [s["port"] for s in finding.services] == [6555]  # the first published port, from .env
    assert "6555" in finding.evidence[-1] and SECRET not in str(finding.evidence)
    assert resolve_port("${OTHER:-6401}") == 6401


def test_the_env_file_is_asked_only_for_a_port(tmp_path):
    (tmp_path / ".env").write_text(f'export WEB_PORT="6600"\nTOKEN={SECRET}\nEMPTY=\nLATER=1\nLATER=2 # the last one counts\n', encoding="utf-8")
    p = Project(tmp_path)
    assert p.env_port("WEB_PORT") == ("port", 6600)
    assert p.env_port("TOKEN") == ("other", None)  # the value itself is never returned
    assert p.env_port("EMPTY") == ("empty", None)
    assert p.env_port("MISSING") == ("unset", None)
    assert p.env_port("LATER") == ("port", 2)
    assert p.env_port("BAD NAME") == ("unset", None)


# --------------------------------------------------------------------------- reading web-server configuration

def test_nginx_routes_simple_forms_only():
    conf = """
    upstream pool { server engine:9000; }
    server {
        # location /commented/ { proxy_pass http://engine:1; }
        location /svc/ { proxy_pass http://engine:8000/; }
        location ^~ /v2 { proxy_pass http://pool; }
        location = /exact { proxy_pass http://engine:8001; }
        location ~ \\.php$ { proxy_pass http://engine:9; }
        location /var/ { proxy_pass http://$backend; }
        location / { try_files $uri /index.html; }
    }
    """
    assert nginx_routes(conf) == [("/svc", "engine", 8000), ("/v2", "engine", 9000), ("/exact", "engine", 8001)]


def test_caddy_routes_simple_forms_only():
    conf = ":80 {\n  reverse_proxy /svc/* engine:8000\n  handle_path /v1/* {\n    reverse_proxy engine:9000\n  }\n  file_server\n}\n"
    assert caddy_routes(conf) == [("/svc", "engine", 8000), ("/v1", "engine", 9000)]


def test_a_route_counts_only_when_it_goes_to_the_same_application(tmp_path):
    project = make_web_app(tmp_path, port_line="6400:80", upstream="elsewhere-host")
    # Rewrite the compose file so the proxied host is not one of its services.
    compose = (project / "docker-compose.yml").read_text().replace("  elsewhere-host:\n", "  engine:\n").replace("- elsewhere-host", "- engine")
    (project / "docker-compose.yml").write_text(compose)
    finding = inspect_docker(Project(project))
    assert all("proxy_routes" not in s for s in finding.services)


def test_proxy_routes_found_from_dockerfile_copy_mount_and_caddy(tmp_path):
    for i, kw in enumerate([{}, {"stock_image": True}, {"web_server": "caddy"}]):
        project = make_web_app(tmp_path / str(i), port_line="6400:80", prefix="/svc", **kw)
        finding = inspect_docker(Project(project))
        web = next(s for s in finding.services if s["service"] == "web")
        assert [r.prefix for r in web["proxy_routes"]] == ["/svc"], kw
        assert web["proxy_routes"][0].upstream == UPSTREAM


def test_config_outside_the_project_is_never_read(tmp_path):
    project = make_web_app(tmp_path, port_line="6400:80")
    (project / "web" / "Dockerfile").write_text("FROM nginx:alpine\nCOPY ../../outside.conf /etc/nginx/conf.d/default.conf\n", encoding="utf-8")
    (project / "web" / "nginx.conf").unlink()
    svc = {"build": "./web", "volumes": ["/etc/passwd:/etc/nginx/nginx.conf"]}
    assert proxy_routes(Project(project), svc, {UPSTREAM, "web"}, "web") == []


# --------------------------------------------------------------------------- the matrix, against running websites

def test_D_G_J_a_website_alone_is_evidence_not_a_way_in(tmp_path, sites):
    site = sites(prefix=None)
    project = make_web_app(tmp_path, port_line=f"127.0.0.1:{site.port}:80", prefix=None)
    draft = discover(project)
    assert draft.web_ui is not None and draft.app_url is None  # found, not given: a surface
    [web] = draft.surfaces
    assert (web.kind, web.role, web.url, web.reach) == ("web_app", "use", f"http://127.0.0.1:{site.port}", "loopback")
    assert draft.can_add  # worth adding: the person uses it through its own app
    assert not draft.invocable and draft.availability == "not_invocable"
    assert draft.runtimes == []  # the page is never ranked as a way to send work
    assert ("GET", "/openapi.json") in site.seen  # looked, and the HTML answer was refused
    public = draft.public()
    assert public["web_ui"] == {"running": True, "title": "Example App", "routes": []}
    assert any("Website: running" in e for e in public["evidence"])
    assert any("only has its own website" in w for w in public["warnings"])
    assert_private(draft)


def test_B_E_website_passing_a_path_to_a_service_that_describes_nothing(tmp_path, sites):
    site = sites(prefix="/api", health=True)
    project = make_web_app(tmp_path, port_line="127.0.0.1:${WEB_PORT:-1}:80", env=f"WEB_PORT={site.port}\n")
    draft = discover(project)
    assert not draft.invocable and draft.runtimes == []
    assert draft.web_ui["routes"] == ["/api"]
    evidence = " | ".join(draft.evidence)
    assert f"publishes port {site.port}" in evidence  # the port came from the expression
    assert "/api goes to another part of the application, which answers" in evidence
    assert "none found (no API description under /api" in evidence
    assert ("GET", "/api/openapi.json") in site.seen and ("GET", "/api/health") in site.seen
    assert_private(draft)


def test_F_L_a_description_behind_the_website_becomes_the_way_in(tmp_path, sites):
    site = sites(prefix="/api", descriptor=READ_ONLY_API)
    project = make_web_app(tmp_path, port_line=f"{site.port}:80")
    draft = discover(project)
    rt = draft.runtime
    assert draft.invocable and rt.availability == "ready" and rt.kind.value == "docker_compose"
    assert rt.adapter["kind"] == "openapi"
    config = rt.adapter["config"]
    # Its operations already start with /api: they are called from the origin.
    assert config["base_url"] == f"http://127.0.0.1:{site.port}"
    assert config["descriptor_url"] == f"http://127.0.0.1:{site.port}/api/openapi.json"
    assert config["health_path"] == "/api/health"
    # "/api/openapi.json" is one of Bevro's standard places, so this one is
    # found at the website's own address; another prefix needs the
    # configuration (see the "/svc" test below).
    assert not any(e.startswith("Website:") for e in draft.evidence)
    assert [c.title for c in draft.capabilities]  # derived from its operations, generically
    assert_private(draft)


def test_I_K_a_prefix_stripping_proxy_and_another_prefix(tmp_path, sites):
    site = sites(prefix="/svc", descriptor=stripped(READ_ONLY_API, "/api"), descriptor_path="/svc/openapi.json")
    project = make_web_app(tmp_path, port_line=f"{site.port}:80", prefix="/svc", strip=True)
    draft = discover(project)
    config = draft.runtime.adapter["config"]
    # The service behind sees "/documents"; from outside that is "/svc/documents".
    assert config["base_url"] == f"http://127.0.0.1:{site.port}/svc"
    assert config["health_path"] == "/health"
    assert draft.invocable
    # The prefix came from the configuration. The only "/api" paths asked for
    # are Bevro's own two standard checks, asked of every address.
    assert {path for _m, path in site.seen if path.startswith("/api")} <= {"/api/openapi.json", "/api/health"}
    assert ("GET", "/svc/openapi.json") in site.seen


def test_H_a_published_api_is_read_as_before(tmp_path, sites):
    site = sites(prefix=None, root_descriptor=READ_ONLY_API, page=False)
    project = tmp_path / "direct"
    project.mkdir()
    (project / "docker-compose.yml").write_text(f'services:\n  api:\n    build: .\n    ports:\n      - "{site.port}:8000"\n', encoding="utf-8")
    (project / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    draft = discover(project)
    assert draft.invocable and draft.runtime.adapter["config"]["base_url"] == f"http://127.0.0.1:{site.port}"
    assert draft.web_ui is None


def test_two_described_services_are_both_kept(tmp_path, sites):
    a, b = sites(prefix=None, root_descriptor=READ_ONLY_API, page=False), sites(prefix=None, root_descriptor=READ_ONLY_API, page=False)
    project = tmp_path / "pair"
    project.mkdir()
    (project / "docker-compose.yml").write_text(
        f'services:\n  api:\n    build: .\n    ports:\n      - "{a.port}:8000"\n  service:\n    build: .\n    ports:\n      - "{b.port}:8000"\n',
        encoding="utf-8",
    )
    (project / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    draft = discover(project)
    usable = [rt for rt in draft.runtimes if rt.invocable]
    assert len(usable) == 2 and draft.runtime in usable


def test_E_a_proxied_service_that_does_not_answer_is_not_usable(tmp_path, sites):
    site = sites(prefix="/api", health=False)
    project = make_web_app(tmp_path, port_line=f"{site.port}:80")
    draft = discover(project)
    assert not draft.invocable and draft.runtimes == []
    assert any(e.endswith("goes to another part of the application") for e in draft.evidence)


def test_a_stock_web_server_image_in_front_is_not_skipped_as_infrastructure(tmp_path, sites):
    site = sites(prefix="/api", descriptor=READ_ONLY_API)
    project = make_web_app(tmp_path, port_line=f"{site.port}:80", stock_image=True)
    assert discover(project).invocable


def test_K_a_command_is_not_displaced_by_an_unrelated_website(tmp_path, sites):
    site = sites(prefix=None)
    project = make_web_app(tmp_path, port_line=f"{site.port}:80", prefix=None, cli=True)
    draft = discover(project)
    assert draft.runtime is not None and draft.runtime.kind.value in ("cli", "python_entrypoint")
    assert draft.invocable and draft.web_ui is not None
    # Its containers may still be places to run the command; the website is never a way in.
    assert all(rt.context is not None for rt in draft.runtimes if rt.kind.value == "docker_compose")
    assert all(rt.adapter.get("kind") != "http" for rt in draft.runtimes)


def test_nothing_published_changes_nothing(tmp_path):
    project = make_web_app(tmp_path, port_line=f"{free_port()}:80")
    draft = discover(project)  # nothing listens there
    assert draft.web_ui is None
    assert [rt.availability for rt in draft.runtimes] == ["needs_start"]
