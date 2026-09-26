"""Local project discovery: look, do not touch.

Resolves the typed path inside the approved roots, runs the inspectors and
probes, and turns what they found into one ProviderDraft with *every* runtime
it could see, ranked:

    already running (a process of yours listening on a port, or a service that answers)
    managed (Compose service that answers; systemd unit)
    declared MCP / CLI interfaces
    inferred entry points
    a web service that is not running yet (connect by address once started)

The project itself is never written to, and the draft the browser sees
carries no absolute path.
"""

from __future__ import annotations

import os
from pathlib import Path

from adapters.localroots import OutsideRoots, find_by_name, resolve_within
from adapters.runtime import ContextInput, CredentialStrategy, Credentials, RuntimeKind, RuntimeProfile
from app.connect.capabilities import infer_capabilities
from app.connect.draft import ProviderDraft, not_found
from app.connect.inspect import Project, humanise, readme_body, readme_excerpt
from app.connect.probes import SystemdUnit, listening_processes, local_base, port_answers, systemd_units
from app.connect.contexts import WORK_INTERFACES, compose_context, link_contexts, systemd_context
from app.connect.runtimes import cli_runtime, http_runtime, managed_only_runtime, mcp_runtime, select, unique_id
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed
from app.connect.strategies.docker import inspect_docker
from app.connect.strategies.node import inspect_node
from app.connect.strategies.project import SECRET_LABELS, Entrypoint, Finding
from app.connect.strategies.python import inspect_python
from app.connect.strategies.scripts import inspect_scripts
from app.connect.surfaces import source_texts
from app.connect.targets import ConnectTarget, classify_target
from app.connect.uicopy import ui_copy

# Bevro asks for permission when it needs it, so neither of these should
# reach anybody in the ordinary course of things. They are what is said if
# something is looked at without having been allowed - which would be a bug,
# and is worth saying in words a person can act on rather than in the name of
# a variable they have never heard of.
NO_ROOTS_MESSAGE = "Bevro hasn't been allowed to look at anything on this machine yet."
OUTSIDE_MESSAGE = "Bevro hasn't been allowed to look in that folder."
WEB_FRAMEWORKS = ("fastapi", "flask", "starlette", "django", "express", "fastify", "koa", "hono", "@nestjs/core", "next", "nuxt")
MAX_ENTRYPOINT_RUNTIMES = 4


def resolve_target(target: ConnectTarget, roots: list[Path]) -> Path:
    if not roots:
        raise DiscoveryFailed(NO_ROOTS_MESSAGE)
    value = target.value
    if target.kind == "name" or "bare" in target.hints:
        found = find_by_name(value, roots)
        if found is None:
            raise DiscoveryFailed(f"Bevro couldn't find a folder called '{value}'. Give the full path to it.")
        return found
    if "relative" in target.hints:
        for root in roots:
            candidate = root / value
            if candidate.is_dir():
                try:
                    return resolve_within(str(candidate), roots)
                except OutsideRoots:
                    continue
        raise DiscoveryFailed(f"Bevro couldn't find a folder called '{value}'. Give the full path to it.")
    try:
        path = resolve_within(value, roots)
    except OutsideRoots as exc:
        message = str(exc)
        if "does not exist" in message:
            raise DiscoveryFailed(f"That folder doesn't exist: {Path(os.path.expanduser(value)).name}") from exc
        raise DiscoveryFailed(OUTSIDE_MESSAGE) from exc
    if not path.is_dir():
        raise DiscoveryFailed("That is a file, not a folder.")
    return path


class LocalProjectStrategy:
    name = "local"

    def __init__(self, *, probe_host: bool = True) -> None:
        # Tests may turn the host probes off; discovery is then purely static.
        self.probe_host = probe_host

    def supports(self, target: ConnectTarget) -> bool:
        # A name reaches here only from a provider connected by one before
        # Bevro learned to search for names: it is looked for, as it was
        # then, directly under the folders the person allowed.
        return target.kind in ("local", "name")

    def discover(self, target: ConnectTarget, context: DiscoveryContext) -> ProviderDraft:
        root = resolve_target(target, context.roots)
        project = Project(root)
        findings = [f for f in (inspect_python(project), inspect_node(project), inspect_docker(project), inspect_scripts(project)) if f is not None]
        units = systemd_units(project) if self.probe_host else []
        return compose_draft(project, findings, units, context, probe_host=self.probe_host)


# --------------------------------------------------------------------------- credentials

def credential_source(project: Project, primary: Finding | None, name: str) -> str:
    """"project" (it loads its own .env defining the name), "host" (the machine
    doing the work has it in the environment) or "missing"."""
    if primary is not None and primary.loads_dotenv and project.declares_env_var(name):
        return "project"
    if name in os.environ:
        return "host"
    return "missing"


def cli_credentials(project: Project, primary: Finding | None) -> tuple[Credentials, list[str], list[str]]:
    """(credentials for a CLI runtime, self-configured names, evidence lines)."""
    if not primary or not primary.env_secret_names:
        return Credentials(strategy=CredentialStrategy.NONE), [], []
    secret = primary.env_secret_names[0]
    label = SECRET_LABELS.get(secret, secret.replace("_", " ").title())
    source = credential_source(project, primary, secret)
    if source == "project":
        note = f"Uses its own {label} from the project's .env; Bevro never reads it."
        return Credentials(strategy=CredentialStrategy.PROJECT_DOTENV, names=[secret], supplied=[secret], note=note), [secret], [f"Uses its own {label} (the project loads its .env)"]
    if source == "host":
        note = f"Uses the {label} already set on this machine."
        return Credentials(strategy=CredentialStrategy.INHERITED_ENVIRONMENT, names=[secret], supplied=[secret], note=note), [], [f"Uses the {label} already set on this machine"]
    # Nothing this way in can reach. Whether anyone has to be asked depends on
    # the other ways in, which are not known yet - `settle_credentials` below
    # decides once they all are.
    note = f"Passed to the agent as {secret} when it runs. Stored encrypted; never shown again."
    return Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=[secret], required_from_user=True, note=note), [], []


def _compose_credentials(service: dict, names: list[str]) -> Credentials:
    """A Compose service carries its own environment; Bevro just reaches it."""
    if not names:
        return Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED)
    env_keys = set()
    env = service.get("environment") or {}
    if isinstance(env, dict):
        env_keys |= {str(k) for k in env}
    elif isinstance(env, list):
        env_keys |= {str(e).split("=", 1)[0] for e in env}
    if service.get("env_file") or any(n in env_keys for n in names):
        # Its compose file says where they come from, so the service has them
        # whether or not Bevro could name which.
        return Credentials(strategy=CredentialStrategy.DOCKER_ENVIRONMENT, names=names, supplied=list(names), held_for="its container", note="Uses the credentials it was set up with.")
    return Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, names=names)


# --------------------------------------------------------------------------- the draft

def compose_draft(project: Project, findings: list[Finding], units: list[SystemdUnit] | None = None, context: DiscoveryContext | None = None, *, probe_host: bool = True) -> ProviderDraft:
    units = units or []
    readme_title, readme_prose = readme_excerpt(project)
    # A language project describes the provider best; a bare scripts project stands in for it.
    primary = next((f for f in findings if f.kind in ("python", "node")), None) or next((f for f in findings if f.kind == "script"), None)
    scripts = next((f for f in findings if f.kind == "script"), None)
    docker = next((f for f in findings if f.kind == "docker"), None)
    evidence: list[str] = []
    warnings: list[str] = []
    for f in findings:
        evidence += f.evidence
        warnings += f.warnings
    if readme_prose:
        evidence.append("README describes the project")

    raw_name = (primary.name if primary else None) or readme_title or project.name
    name = readme_title if readme_title and len(readme_title) <= 60 and not readme_title.lower().startswith(("welcome", "getting started")) else humanise(raw_name)
    description = (primary.description if primary else None) or readme_prose or ""
    if readme_prose and description.strip().lower() in (name.lower(), raw_name.lower()):
        description = readme_prose
    description = " ".join(description.split())[:300]
    capabilities = infer_capabilities(name, description, readme_prose, readme_body(project), weights=(2.0, 2.0, 1.0, 0.34))
    sources = source_texts(project)
    if not description:
        # Nothing written about it; what it says to its own users will do.
        on_screen = ui_copy(project, sources)
        if on_screen:
            description = " ".join(on_screen[:2])[:300]
            evidence.append("Its own screens say what it's for")
            capabilities = capabilities or infer_capabilities(name, description, " ".join(on_screen), weights=(2.0, 2.0, 1.0))

    if not findings and not units:
        draft = not_found(name, "local", "I found this folder, but it doesn't look like a Python, Node or Docker project.", evidence)
        draft.description = description
        return draft

    ids: set[str] = set()
    runtimes: list[RuntimeProfile] = []
    secret_names = list(primary.env_secret_names) if primary else []
    frameworks = primary.frameworks if primary else set()
    lang = "Python" if primary and primary.kind == "python" else "Node" if primary and primary.kind == "node" else "Local"

    # A website is evidence that the application is running, and where to
    # open it; a way to send it work only if something behind it says so.
    website: dict | None = None
    # A running API that serves several profiles (workspaces, tenants...)
    # asks which one this connection is for, as it would if connected by address.
    scope_choices: list[dict[str, str]] = []

    # 1. Already running: a process of yours in this folder that listens on a port.
    probed_ports: set[int] = set()
    if probe_host:
        for proc in listening_processes(project.root):
            for port in proc.ports:
                if port in probed_ports:
                    continue
                probed_ports.add(port)
                # Where it actually listens: loopback only reaches a service bound to every address.
                base = local_base(port, proc.addresses.get(port))
                found = _discover_url(base, context)
                if found is not None and _described_nothing(found) and found.web_ui is not None:
                    # A page for people, served by a process of this project.
                    website = website or {"url": base, "title": found.web_ui.get("title"), "routes": [], "bind": None, "evidence": [f"A process from this project ({proc.program}) serves a website on port {port}"]}
                    continue
                if found is not None:
                    rt = _adopt_service(found, unique_id("running", ids), RuntimeKind.PROCESS, "Already running on this machine", evidence=[f"A process from this project ({proc.program}) is listening on port {port}", *found.evidence], credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, names=secret_names, supplied=list(secret_names), note="Already running with its own environment."))
                    runtimes.append(rt)
                    scope_choices = scope_choices or list(found.scope_choices)
                    # What the running service itself declares it can do is
                    # stronger evidence than words picked out of its README.
                    capabilities = found.capabilities or capabilities
                    description = description or found.description

    # 2. Managed: Compose services (answering, or startable) and systemd units.
    for service in (docker.services if docker else []):
        port = int(service["port"])
        answering = probe_host and port not in probed_ports and port_answers(port, address=service.get("address"))
        found, site = _discover_service(service, context) if answering else (None, None)
        creds = _compose_credentials(service.get("svc") or {}, secret_names) if service.get("start") == "compose" else Credentials(strategy=CredentialStrategy.DOCKER_ENVIRONMENT if secret_names else CredentialStrategy.RUNTIME_MANAGED, names=secret_names)
        if site is not None and website is None:
            website = {**site, "bind": service.get("bind")}
            evidence += site["evidence"]
        if found is not None:
            runtimes.append(_adopt_service(found, unique_id("compose", ids), RuntimeKind.DOCKER_COMPOSE, "Runs as a local service (already running)", evidence=[service["source"], "It answers on that port", *found.evidence], credentials=creds))
            capabilities = found.capabilities or capabilities
            scope_choices = scope_choices or list(found.scope_choices)
        elif site is not None:
            continue  # running, with a website and nothing behind it that takes work
        else:
            how = "docker compose up -d" if service.get("start") == "compose" else "start its container"
            runtimes.append(
                http_runtime(
                    unique_id("compose", ids),
                    kind=RuntimeKind.DOCKER_COMPOSE,
                    adapter={"kind": "http", "config": {"base_url": f"http://localhost:{port}", "health_path": service.get("health_path")}},
                    display_name="Runs as a local service",
                    availability="needs_start",
                    confidence="medium",
                    credentials=creds,
                    evidence=[service["source"]],
                    warnings=[f"It isn't running. Bevro won't start it: {how}, then choose Check again so Bevro can read how to use it."],
                )
            )
    for unit in units:
        runtimes.append(_systemd_runtime(unit, unique_id("systemd", ids), secret_names, project.root))

    # 3./4. Declared and inferred entry points: MCP servers and command-line interfaces.
    creds, self_configured, cred_evidence = cli_credentials(project, primary)
    evidence += cred_evidence
    # A language project may also ship a wrapper script; both are ways in.
    candidates = list(primary.entrypoints) if primary else []
    if scripts is not None and scripts is not primary:
        candidates += scripts.entrypoints
    entrypoints = sorted(candidates, key=lambda e: ({"high": 0, "medium": 1, "low": 2}[e.confidence], 0 if e.input_known else 1))
    for entry in entrypoints[:MAX_ENTRYPOINT_RUNTIMES]:
        runtimes.append(_entrypoint_runtime(project, primary, entry, creds, self_configured, unique_id("mcp" if entry.mechanism == "mcp" else "cli", ids), lang))

    # 5. A web framework with nothing running and no Compose: connect by address once started.
    if not any(rt.kind in (RuntimeKind.PROCESS, RuntimeKind.DOCKER_COMPOSE) for rt in runtimes) and any(fw in frameworks for fw in WEB_FRAMEWORKS):
        port = 8000 if frameworks & {"fastapi", "flask", "starlette", "django"} else 3000
        runtimes.append(
            http_runtime(
                unique_id("service", ids),
                kind=RuntimeKind.HTTP,
                adapter={"kind": "http", "config": {"base_url": f"http://localhost:{port}", "health_path": None}},
                display_name="Runs as a local service",
                availability="needs_start",
                confidence="low",
                credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, names=secret_names),
                evidence=["It is a web service (framework found in the project)"],
                warnings=[f"It runs as a web service on port {port}. Bevro won't start it: start it as you normally do, then choose Check again so Bevro can read how to use it."],
            )
        )

    # Contexts a program could be launched inside: one-off containers of the
    # project's Compose services, then which program each context would carry.
    interfaces = [rt for rt in runtimes if rt.context is None and rt.kind in WORK_INTERFACES]
    runtimes += _container_runtimes(docker.containers if docker else [], secret_names, project.root, interfaces, ids)
    link_contexts(runtimes)

    surfaces = _surfaces(project, findings, primary, website, runtimes, sources, probe_host=probe_host)

    if not runtimes:
        from app.connect.bridge import callable_surface

        surface = callable_surface(project, findings)
        draft = not_found(name, "local", WEBSITE_ONLY if website else "This project doesn't expose a connection Bevro can use yet.", evidence)
        if website:
            draft.web_ui = _public_site(website)
        draft.description = description
        draft.capabilities = capabilities
        draft.warnings += warnings
        draft.callable_evidence = {k: v for k, v in surface.items() if v}
        draft.assist_evidence = {"readme_excerpt": readme_prose or "", "dependencies": (primary.dependencies if primary else [])[:30]}
        draft.surfaces = surfaces
        return draft

    # Which way in is used is decided by `with_runtimes`, after credentials
    # have been settled across all of them. Only "is this close enough to ask
    # about" is decided here.
    _first, choice = select(runtimes)
    draft = ProviderDraft(name=name, description=description, capabilities=capabilities, mechanism="local", evidence=evidence, warnings=warnings, confidence="medium", surfaces=surfaces)
    draft.scope_choices = scope_choices
    if website:
        draft.web_ui = _public_site(website)
    draft.with_runtimes(runtimes, None, choice)
    rt = draft.runtime
    if rt is not None:
        uses = next((e for e in rt.evidence if e.startswith("Bevro will use: ")), None)
        draft.invocation_label = uses.removeprefix("Bevro will use: ") if uses else rt.display_name
        draft.evidence = [*evidence, *[e for e in rt.evidence if not e.startswith("Bevro will use: ")]]
        draft.confidence = rt.confidence if rt.invocable else "low"
        draft.warnings = [*warnings, *rt.warnings]
        if not capabilities:
            draft.confidence = "low"
        if not rt.invocable and len(runtimes) == 1:
            draft.warnings = [*draft.warnings, "I found how it runs, but not a way to hand it a task."]
    draft.assist_evidence = {
        "readme_excerpt": readme_prose or "",
        "dependencies": (primary.dependencies if primary else [])[:30],
        "runtimes": [f"{r.id}: {r.display_name} ({r.kind.value}, {r.availability})" for r in draft.runtimes][:8],
    }
    if not any(rt.invocable for rt in draft.runtimes):
        # Everything found is a way of *running* the project, not of giving it
        # work. A connection of Bevro's own may still be possible.
        from app.connect.bridge import callable_surface

        surface = callable_surface(project, findings)
        draft.callable_evidence = {k: v for k, v in surface.items() if v}
    return draft


def _surfaces(project: Project, findings: list[Finding], primary: Finding | None, website: dict | None, runtimes: list[RuntimeProfile], sources: list[str], *, probe_host: bool) -> list:
    """How a person uses this, apart from Bevro: see connect/surfaces.py."""
    from app.connect import surfaces as surface
    from app.connect.probes import private_shares, systemd_timers

    web = []
    if website:
        web = [surface.web_surface(website["url"], bind=website.get("bind"), title=website.get("title"), evidence=list(website.get("evidence") or [])[:1], shares=private_shares() if probe_host else {})]
    dependencies = sorted({d for f in findings for d in f.dependencies})
    env_names = [*project.env_example_keys(), *(primary.env_secret_names if primary else [])]
    chat = surface.messaging_surfaces(dependencies, sources, env_names, readme_body(project))
    timers = surface.schedule_surfaces(project, systemd_timers(project, ask_systemd=probe_host))
    cli = [surface.Surface(kind="command_line", role="use", confidence="medium", evidence=["It has a command-line entry point"])] if any(rt.kind in (RuntimeKind.CLI, RuntimeKind.PYTHON_ENTRYPOINT, RuntimeKind.NODE_ENTRYPOINT) and rt.invocable for rt in runtimes) else []
    return surface.merge(web, chat, timers, cli)


WEBSITE_ONLY = "It's running on this machine with its own website. Bevro found nothing there it can send tasks to."


def _discover_url(url: str, context: DiscoveryContext | None) -> ProviderDraft | None:
    """Read what answers at a local address the way a URL target is read."""
    from app.connect.strategies.http import HttpDiscoveryStrategy

    try:
        ctx = DiscoveryContext(roots=[], secrets=dict(context.secrets) if context else {}, timeout=3.0, transport=context.transport if context else None)
        found = HttpDiscoveryStrategy().discover(classify_target(url), ctx)
    except DiscoveryFailed:
        return None
    return found if found.runtime is not None else None


def _described_nothing(draft: ProviderDraft) -> bool:
    return (draft.source or {}).get("discovery_method") == "none"


def _discover_service(service: dict, context: DiscoveryContext | None) -> tuple[ProviderDraft | None, dict | None]:
    """What a running Compose service offers: (a way to send it work, its website).

    What answers on the published port is read first. If that is a page for
    people, the paths its own web-server configuration passes on to another
    part of the application are read the same way - the only places behind
    it worth looking, and the only ones looked at. A way in is whatever
    describes itself; a website is kept as evidence either way.
    """
    base = local_base(int(service["port"]), service.get("address"))
    root = _discover_url(base, context)
    if root is None:
        return None, None
    if not _described_nothing(root) or root.web_ui is None:
        # It describes itself, or it is not a page for people: read as always.
        return root, None
    site: dict = {"url": base, "title": root.web_ui.get("title"), "routes": [], "evidence": ["Website: running on this machine"]}
    for route in service.get("proxy_routes") or []:
        site["routes"].append(route.prefix)
        behind = _discover_url(base + route.prefix + "/", context)
        if behind is not None and not _described_nothing(behind):
            behind.evidence = [f"Its website passes {route.prefix} on to another part of the application, which describes itself", *behind.evidence]
            return behind, site
        answers = behind is not None and bool((behind.source or {}).get("health_path"))
        site["evidence"].append(f"Behind the website: {route.prefix} goes to another part of the application" + (", which answers" if answers else ""))
    site["evidence"].append(
        "Ways for programs to send it work: none found"
        + (f" (no API description under {', '.join(site['routes'])} or at the website's root)" if site["routes"] else " (the website publishes no API description)")
    )
    return None, site


def _public_site(site: dict) -> dict:
    return {"url": site["url"], "title": site.get("title"), "routes": list(site.get("routes") or [])}


def _adopt_service(found: ProviderDraft, rid: str, kind: RuntimeKind, display_name: str, *, evidence: list[str], credentials: Credentials) -> RuntimeProfile:
    """The runtime of a service that answered, re-labelled as what it is for this project."""
    base = found.runtime
    assert base is not None
    creds = credentials
    if base.credentials.required_from_user:
        creds = base.credentials  # the service itself wants a token from Bevro
    if base.kind == RuntimeKind.MCP_HTTP:
        return mcp_runtime(rid, stdio=False, adapter=base.adapter, display_name=display_name + " · MCP", availability="ready", confidence=base.confidence, credentials=creds, evidence=evidence, warnings=list(base.warnings), target=base.target)
    return http_runtime(rid, kind=kind, adapter=base.adapter, display_name=display_name, availability="ready" if base.invocable else "not_invocable", confidence=base.confidence, credentials=creds, evidence=evidence, warnings=list(base.warnings), target=base.target, accepts_prompt=base.invocable)


def _systemd_runtime(unit: SystemdUnit, rid: str, secret_names: list[str], owner: Path) -> RuntimeProfile:
    if unit.template:
        state = "installed, started once per use" if unit.installed else "shipped with the project, not installed here"
    else:
        state = "active" if unit.active else "installed but not running" if unit.installed else "shipped with the project, not installed here"
    evidence = [f"systemd unit {unit.name}: {state}"]
    creds = _systemd_credentials(unit, secret_names)
    if creds.supplied:
        evidence.append(_credential_evidence(unit, creds))
        creds = creds.model_copy(update={"held_for": "its scheduled runs" if unit.unit_type == "oneshot" else "its installed service"})
    context = systemd_context(unit, owner, creds)
    if context.input == ContextInput.STDIN:
        evidence.append(f"{unit.socket.name if unit.socket else unit.name} starts it for each connection and hands it the request")
        note = "It takes requests on a socket, which Bevro doesn't hand work to yet."
    elif unit.unit_type == "oneshot" or not unit.active:
        note = "It's managed by systemd as a scheduled or one-off job, which Bevro can't hand tasks to." if unit.unit_type == "oneshot" else "Its systemd unit isn't running, so there is nothing to reach."
    else:
        note = "Its systemd service is running but doesn't expose a way to hand it tasks."
    return managed_only_runtime(rid, kind=RuntimeKind.SYSTEMD, display_name="Runs as a local service (systemd)", evidence=evidence, note=note, credentials=creds, target=unit.name, context=context)


def _container_runtimes(containers: list[dict], secret_names: list[str], owner: Path, interfaces: list[RuntimeProfile], ids: set[str]) -> list[RuntimeProfile]:
    """Compose services a one-off container of which could run the project's program.

    Only worth recording beside a program Bevro would run: a context is
    somewhere *that* program could be launched with more than the shell
    gives it. A service that publishes a port and runs something else is
    reached as a service, and already is.
    """
    if not interfaces:
        return []
    out: list[RuntimeProfile] = []
    for container in containers:
        creds = _compose_credentials(container.get("svc") or {}, secret_names)
        context = compose_context(container, owner, creds)
        runtime = managed_only_runtime(
            "compose-run",
            kind=RuntimeKind.DOCKER_COMPOSE,
            display_name="Runs in a container (Compose)",
            evidence=[f"{container['file']}: service '{container['service']}' can be run as a one-off container"],
            note="Bevro doesn't run work in its containers yet.",
            credentials=creds,
            context=context,
        )
        link_contexts([runtime, *interfaces])
        if container.get("ports") and runtime.context.matches is None:
            continue
        runtime.id = unique_id("compose-run", ids)
        out.append(runtime)
    return out[:3]


def _systemd_credentials(unit: SystemdUnit, secret_names: list[str]) -> Credentials:
    """What this unit hands its own process, from what it declares.

    A unit file is configuration and may be read. A file it points at is
    not, and is never opened - the reference is what counts, and a
    credentials file Bevro may not even stat is the clearest evidence there
    is that something is looking after this properly.
    """
    if not secret_names:
        return Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED)
    named = [n for n in secret_names if n in unit.environment_names]
    files = [ref for ref in unit.environment_files if ref.likely_present]
    if files:
        # A file the unit loads supplies whatever the project asks for: which
        # names are in it cannot be known without opening it, and opening it
        # is the one thing Bevro will not do.
        return Credentials(
            strategy=CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE,
            names=secret_names,
            supplied=list(secret_names),
            note="Uses the credentials its installed service provides.",
        )
    if named:
        return Credentials(
            strategy=CredentialStrategy.SYSTEMD_ENVIRONMENT,
            names=secret_names,
            supplied=named,
            note="Its systemd unit sets them itself.",
        )
    dangling = [ref for ref in unit.environment_files if not ref.likely_present and not ref.optional]
    if dangling:
        return Credentials(
            strategy=CredentialStrategy.UNKNOWN,
            names=secret_names,
            note="Its systemd unit points at a credentials file that isn't there.",
        )
    return Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, names=secret_names)


def _credential_evidence(unit: SystemdUnit, creds: Credentials) -> str:
    """Said without naming the file: where it is is the machine's business."""
    if creds.strategy == CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE:
        return f"{unit.name} loads its credentials from a file of its own"
    return f"{unit.name} sets {', '.join(creds.supplied)} itself"


def _entrypoint_runtime(project: Project, primary: Finding | None, entry: Entrypoint, creds: Credentials, self_configured: list[str], rid: str, lang: str) -> RuntimeProfile:
    input_mode = dict(entry.input)
    warnings: list[str] = []
    if entry.mechanism == "mcp":
        adapter = {"kind": "mcp", "config": {"argv": list(entry.argv), "cwd": str(project.root), "env": dict(primary.env) if primary else {}, "secret_env": list(creds.names), "self_configured": self_configured, "tool": {}}}
        return mcp_runtime(rid, stdio=True, adapter=adapter, display_name="Uses MCP on this machine", availability="needs_worker", confidence="medium", credentials=creds, evidence=[f"Entry point: {entry.source}"], warnings=["Choose Test to start the server once and read its tools; nothing in the project is changed."], target=str(project.root))
    if not entry.input_known:
        warnings.append("I couldn't tell how this program takes a request; it will be passed as the last argument. Change that under Advanced setup if needed.")
    adapter = {
        "kind": "command",
        "config": {
            "argv": list(entry.argv),
            "cwd": str(project.root),
            "input": input_mode,
            "output": {"modes": ["stdout", "files"]},
            "env": dict(primary.env) if primary else {},
            "secret_env": list(creds.names),
            "self_configured": self_configured,
            "timeout_seconds": 1800,
            **({"self_check": list(entry.self_check)} if entry.self_check else {}),
        },
    }
    kind = RuntimeKind(entry.runtime_kind) if entry.runtime_kind else (RuntimeKind.PYTHON_ENTRYPOINT if lang == "Python" else RuntimeKind.NODE_ENTRYPOINT if lang == "Node" else RuntimeKind.CLI)
    shown = f" {input_mode['flag']} \"…\"" if input_mode.get("mode") == "flag" else (" \"…\"" if input_mode.get("mode") == "argument" else "")
    return cli_runtime(rid, kind=kind, adapter=adapter, display_name="Runs from this project", availability="needs_worker", confidence=entry.confidence, credentials=creds, evidence=[f"Entry point: {entry.source}", f"Bevro will use: {entry.label}{shown}"], warnings=warnings, target=str(project.root))
