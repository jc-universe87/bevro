"""Execution contexts: found, described, and never used - yet.

A project's command line may have no key while its installed service is
handed one. The service is an *execution context*: something that launches a
program with an environment of its own. Bevro now finds these and says
precisely whether one could carry its work:

    A  a socket-activated unit, stdin, the same program     compatible
    B  a fixed one-off job                                  not: no way to ask
    C  anything that needs administrator permission         never
    D  a Compose service whose entrypoint is the program    compatible
    E  a Compose service whose entrypoint is a shell        not: it runs anything
    F  a context supplying some of what is needed           says which
    G  a context that has gone                              gone from the record
    H  a project with none of this                          exactly as before

And for all of them, the rule of this milestone: nothing runs through a
context, so no context is ever chosen for work, and no context's credentials
count as the answer to anybody's question.

Every host fact - is the unit installed, would PolicyKit allow it, may this
user connect to the socket, may it use Docker - is stubbed, so the suite
means the same on a bare runner as on a machine with services installed.
"""

from __future__ import annotations

import builtins
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from adapters import runtime as runtime_module
from adapters.runtime import ContextInput, ContextKind, CredentialStrategy, Credentials, ExecutionContext, RuntimeKind, RuntimeProfile
from app.connect import probes
from app.connect.contexts import program_is_fixed, same_program
from app.connect.runtimes import rank, select, settle_credentials

KEY = "OPENAI_API_KEY"
OTHER = "ANTHROPIC_API_KEY"
SECRET = "fixture-secret-never-read"
PYTHON = shutil.which("python3") or "/usr/bin/python3"


# --------------------------------------------------------------------------- the machine, stubbed

class Host:
    """What the machine would say, set per test."""

    def __init__(self) -> None:
        self.installed: dict[str, str] = {}  # unit name -> "system" | "user"
        self.may_manage = False
        self.socket_ok: bool | None = True
        self.docker_ok: bool | None = True
        self.commands: list[list[str]] = []


@pytest.fixture
def host(monkeypatch):
    h = Host()

    def unit_state(name):
        scope = h.installed.get(name)
        return (None, True, scope) if scope else (None, False, None)

    monkeypatch.setattr(probes, "_unit_state", unit_state)
    monkeypatch.setattr(probes, "_unit_files", lambda name, scope: [])  # the shipped files are the truth
    monkeypatch.setattr(probes, "may_manage_system_units", lambda: h.may_manage)
    monkeypatch.setattr(probes, "socket_permits", lambda listen: h.socket_ok)
    monkeypatch.setattr(probes, "docker_permitted", lambda: h.docker_ok)

    # Anything discovery would *run* is recorded, so the guards below can
    # say what was never run.
    real_run = subprocess.run

    def watched(argv, *args, **kwargs):
        h.commands.append([str(a) for a in argv])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", watched)
    return h


@pytest.fixture
def roots(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv(OTHER, raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def discover(project: Path, roots: Path):
    from app.connect.strategies.base import DiscoveryContext
    from app.connect.strategies.local import LocalProjectStrategy
    from app.connect.targets import classify_target

    return LocalProjectStrategy().discover(classify_target(str(project)), DiscoveryContext(roots=[roots]))


def a_project(root: Path, name: str, *, deps: tuple[str, ...] = ("openai",)) -> Path:
    """A Python project with one program: `python3 -m <package>`, taking a request."""
    project = root / name
    project.mkdir(parents=True)
    deps_text = ", ".join(f'"{d}"' for d in deps)
    (project / "pyproject.toml").write_text(f'[project]\nname = "{name}"\ndescription = "Writes briefs"\ndependencies = [{deps_text}]\n', encoding="utf-8")
    (project / "README.md").write_text(f"# {name}\n\nWrites briefs about widgets.\n", encoding="utf-8")
    package = project / name.replace("-", "_")
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\np.add_argument("--prompt")\nprint(p.parse_args().prompt)\n', encoding="utf-8"
    )
    return project


def credentials_file(root: Path, name: str) -> Path:
    """Where a deployment keeps one: outside the project, never read by Bevro."""
    held = root.parent / "etc" / name
    held.mkdir(parents=True, exist_ok=True)
    path = held / "credentials"
    path.write_text(f"{KEY}={SECRET}\n", encoding="utf-8")
    return path


def unit(project: Path, filename: str, *lines: str) -> None:
    deploy = project / "deploy"
    deploy.mkdir(exist_ok=True)
    (deploy / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


def module(project: Path) -> str:
    return project.name.replace("-", "_")


def socket_project(roots: Path, name: str = "fx-socket") -> Path:
    """A: a unit started per connection, handed the connection as stdin, that
    runs exactly the program discovery finds - with its credentials file."""
    project = a_project(roots, name)
    unit(
        project, f"{name}@.service",
        "[Service]", "Type=simple", "StandardInput=socket", "StandardOutput=socket",
        f"EnvironmentFile={credentials_file(roots, name)}",
        f"WorkingDirectory={project}", f"ExecStart={PYTHON} -m {module(project)}",
    )
    unit(project, f"{name}.socket", "[Socket]", f"ListenStream=/run/{name}.sock", "Accept=yes", "SocketMode=0660")
    return project


def the(draft, kind: str) -> RuntimeProfile:
    found = [rt for rt in draft.runtimes if rt.context is not None and rt.context.kind == kind]
    assert len(found) == 1, [(rt.id, rt.kind, rt.context) for rt in draft.runtimes]
    return found[0]


def work_interface(draft) -> RuntimeProfile:
    return next(rt for rt in draft.runtimes if rt.context is None and rt.kind in ("cli", "python_entrypoint"))


# --------------------------------------------------------------------------- A

def test_a_a_socket_activated_unit_running_the_same_program_is_compatible(roots, host):
    project = socket_project(roots)
    host.installed = {"fx-socket@.service": "system", "fx-socket.socket": "system"}
    draft = discover(project, roots)

    service = the(draft, "systemd_socket")
    ctx = service.context
    cli = work_interface(draft)
    assert ctx.input == ContextInput.STDIN
    assert ctx.privilege == "same_user"  # the socket's own permissions let this user in
    assert ctx.supplies == [KEY]
    assert ctx.matches == cli.id
    assert ctx.problems == [] and ctx.compatible is True


def test_a_but_compatible_is_not_used_nothing_runs_through_it_yet(roots, host):
    project = socket_project(roots, "fx-socket-unused")
    host.installed = {"fx-socket-unused@.service": "system", "fx-socket-unused.socket": "system"}
    draft = discover(project, roots)

    service = the(draft, "systemd_socket")
    assert service.context.compatible and not service.context.launchable
    assert service.invocable is False
    # The way in chosen is the one Bevro can actually run...
    assert draft.runtime.id == work_interface(draft).id
    # ...and it still needs its own key: a context that cannot be launched is
    # not a credential anybody has.
    assert draft.auth.required is True and draft.auth.secret_name == KEY


# --------------------------------------------------------------------------- B

def test_b_a_fixed_one_off_job_has_the_key_and_no_way_to_be_asked(roots, host):
    """The real case's shape, generically: a oneshot unit with a credentials
    file, running a subcommand of the program the command line runs."""
    project = a_project(roots, "fx-oneshot")
    unit(
        project, "fx-oneshot.service",
        "[Service]", "Type=oneshot", f"EnvironmentFile={credentials_file(roots, 'fx-oneshot')}",
        f"WorkingDirectory={project}", f"ExecStart={PYTHON} -m {module(project)} run",
    )
    host.installed = {"fx-oneshot.service": "system"}
    draft = discover(project, roots)

    ctx = the(draft, "systemd_service").context
    assert ctx.supplies == [KEY]
    assert ctx.input == ContextInput.NONE
    assert "no_request_channel" in ctx.problems and ctx.compatible is False
    # Same program, recognised as such - which is exactly why it is worth
    # saying that it still cannot help.
    assert ctx.matches == work_interface(draft).id
    assert draft.runtime.id == work_interface(draft).id
    assert draft.auth.required is True
    note = work_interface(draft).credentials.note or ""
    assert "already has a credential for its scheduled runs" in note and "isn't available when Bevro starts a new task" in note
    # Said to the person with the provider's name, and with a "why" that
    # names no file, unit or mechanism.
    auth = draft.public()["auth"]
    assert auth["hint"].startswith(draft.name) and auth["why"]
    assert "systemd" not in auth["why"] and "EnvironmentFile" not in auth["why"] and "scheduled service starts" in auth["why"]


def test_b_a_template_s_short_name_is_not_a_request(roots, host):
    project = a_project(roots, "fx-template")
    unit(
        project, "fx-template@.service",
        "[Service]", "Type=oneshot", f"EnvironmentFile={credentials_file(roots, 'fx-template')}",
        f"WorkingDirectory={project}", f"ExecStart={PYTHON} -m {module(project)} retry --state runs/%i.json",
    )
    host.installed = {"fx-template@.service": "system"}
    ctx = the(discover(project, roots), "systemd_service").context
    assert ctx.input == ContextInput.INSTANCE_NAME
    assert "instance_name_only" in ctx.problems and not ctx.compatible


# --------------------------------------------------------------------------- C

def test_c_a_system_service_nobody_may_start_without_a_password_is_never_usable(roots, host, monkeypatch):
    project = a_project(roots, "fx-admin")
    unit(project, "fx-admin.service", "[Service]", "Type=oneshot", f"EnvironmentFile={credentials_file(roots, 'fx-admin')}", f"WorkingDirectory={project}", f"ExecStart={PYTHON} -m {module(project)}")
    host.installed = {"fx-admin.service": "system"}
    host.may_manage = False
    ctx = the(discover(project, roots), "systemd_service").context
    assert ctx.privilege == "needs_admin" and "needs_admin" in ctx.problems

    # Even on a Bevro that could launch every kind of context, this one stays
    # out of reach: the permission is the machine's to give, not Bevro's.
    monkeypatch.setattr(runtime_module, "LAUNCHABLE_CONTEXTS", frozenset(ContextKind))
    assert ctx.launchable is False


def test_c_a_socket_this_user_may_not_connect_to_is_the_same(roots, host, monkeypatch):
    project = socket_project(roots, "fx-socket-closed")
    host.installed = {"fx-socket-closed@.service": "system", "fx-socket-closed.socket": "system"}
    host.socket_ok = False
    service = the(discover(project, roots), "systemd_socket")
    assert service.context.privilege == "needs_admin" and not service.context.compatible
    monkeypatch.setattr(runtime_module, "LAUNCHABLE_CONTEXTS", frozenset(ContextKind))
    assert service.invocable is False


def test_c_a_user_s_own_unit_needs_nobody_s_permission(roots, host):
    project = a_project(roots, "fx-user")
    unit(project, "fx-user.service", "[Service]", "Type=oneshot", f"WorkingDirectory={project}", f"ExecStart={PYTHON} -m {module(project)}")
    host.installed = {"fx-user.service": "user"}
    ctx = the(discover(project, roots), "systemd_service").context
    assert ctx.privilege == "same_user" and "needs_admin" not in ctx.problems


# --------------------------------------------------------------------------- D, E

def compose_project(roots: Path, name: str, service: str) -> Path:
    project = a_project(roots, name)
    (project / "docker-compose.yml").write_text(textwrap.dedent(service), encoding="utf-8")
    (project / ".env").write_text(f"{KEY}={SECRET}\n", encoding="utf-8")
    return project


def test_d_a_compose_service_whose_entrypoint_is_the_program_is_compatible_in_principle(roots, host):
    project = compose_project(roots, "fx-compose", """
        services:
          agent:
            build: .
            entrypoint: ["python3", "-m", "fx_compose"]
            env_file: .env
    """)
    draft = discover(project, roots)
    runtime = the(draft, "compose_run")
    ctx = runtime.context
    assert ctx.input == ContextInput.ARGUMENT_DATA
    assert ctx.matches == work_interface(draft).id
    assert ctx.supplies == [KEY] and ctx.privilege == "same_user"
    assert ctx.compatible is True and ctx.launchable is False
    assert runtime.invocable is False and draft.runtime.id == work_interface(draft).id


def test_d_without_docker_permission_it_is_not(roots, host):
    project = compose_project(roots, "fx-compose-closed", """
        services:
          agent:
            build: .
            entrypoint: ["python3", "-m", "fx_compose_closed"]
    """)
    host.docker_ok = False
    ctx = the(discover(project, roots), "compose_run").context
    assert "needs_admin" in ctx.problems and not ctx.compatible


@pytest.mark.parametrize(
    ("name", "service", "dockerfile", "problem"),
    [
        # A shell runs whatever it is given.
        ("fx-shell", 'entrypoint: ["sh", "-c", "python3 -m fx_shell"]', None, "program_not_fixed"),
        # An interpreter with nothing to run would run its arguments.
        ("fx-bare", 'entrypoint: ["python3"]', None, "program_not_fixed"),
        # No entrypoint: `compose run` would run whatever Bevro named.
        ("fx-none", "command: python3 -m fx_none", None, "program_not_fixed"),
        # The shell form of a Dockerfile ENTRYPOINT goes through /bin/sh.
        ("fx-docker-shell", "command: ignored", "FROM python:3.12\nENTRYPOINT python3 -m fx_docker_shell\n", "program_not_fixed"),
    ],
)
def test_e_a_compose_service_that_would_run_anything_is_not_compatible(roots, host, name, service, dockerfile, problem):
    project = compose_project(roots, name, f"services:\n  agent:\n    build: .\n    {service}\n")
    if dockerfile:
        (project / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    ctx = the(discover(project, roots), "compose_run").context
    assert problem in ctx.problems and not ctx.compatible


def test_e_the_exec_form_of_a_dockerfile_entrypoint_is_read_as_the_program(roots, host):
    project = compose_project(roots, "fx-docker-exec", "services:\n  agent:\n    build: .\n")
    (project / "Dockerfile").write_text('FROM python:3.12\nENTRYPOINT ["python3", "-m", "fx_docker_exec"]\n', encoding="utf-8")
    draft = discover(project, roots)
    assert the(draft, "compose_run").context.matches == work_interface(draft).id


def test_e_a_web_service_that_runs_something_else_is_left_as_the_service_it_is(roots, host):
    project = compose_project(roots, "fx-web", """
        services:
          web:
            build: .
            ports: ["58123:8000"]
            entrypoint: ["uvicorn", "app:app"]
    """)
    draft = discover(project, roots)
    assert not any(rt.context is not None for rt in draft.runtimes)


# --------------------------------------------------------------------------- F

def test_f_a_context_that_supplies_some_of_what_is_needed_says_which(roots, host, seeded):
    project = a_project(roots, "fx-partial", deps=("openai", "anthropic"))
    unit(
        project, "fx-partial.service",
        "[Service]", "Type=oneshot", f"Environment={KEY}={SECRET}", "Environment=TZ=UTC",
        f"WorkingDirectory={project}", f"ExecStart={PYTHON} -m {module(project)}",
    )
    host.installed = {"fx-partial.service": "user"}
    draft = discover(project, roots)
    service = the(draft, "systemd_service")
    assert service.context.supplies == [KEY]
    assert set(service.credentials.names) == {KEY, OTHER} and service.credentials.missing == [OTHER]

    from app.services.contexts import describe

    shown = describe(seeded, service)
    assert shown["credentials"] == f"Provides {KEY} · not {OTHER}"
    assert SECRET not in str(shown)


# --------------------------------------------------------------------------- G

def test_g_a_context_that_has_gone_is_gone_from_the_record(roots, host, seeded):
    from app.services import connect as connect_service
    from app.services import providers as provider_service
    from app.services import runtime as runtime_service
    from app.services import trust as trust_service

    project = socket_project(roots, "fx-gone")
    host.installed = {"fx-gone@.service": "system", "fx-gone.socket": "system"}
    draft = discover(project, roots)
    trust_service.grant(seeded, trust_service.FOLDER, str(project))
    provider = provider_service.register_provider(
        seeded,
        {"name": "Gone", "description": "Writes briefs.", "capabilities": [{"id": "research", "title": "Research"}],
         "adapter": draft.runtime.adapter, "origin": "connected", "source": {"kind": "local", "target_kind": "local", "target": str(project)}},
    )
    runtime_service.set_runtimes(provider, draft.runtimes, draft.runtime.id)
    seeded.commit()
    assert any(rt.context is not None for rt in runtime_service.runtimes_of(provider))

    for leftover in (project / "deploy").iterdir():
        leftover.unlink()
    host.installed = {}
    connect_service.rediscover(seeded, provider)

    assert not any(rt.context is not None for rt in runtime_service.runtimes_of(provider))
    from app.schemas.serialise import provider_details

    assert provider_details(provider, seeded).contexts == []


# --------------------------------------------------------------------------- H

def test_h_a_project_with_no_context_is_exactly_what_it_was(roots, host, seeded):
    project = a_project(roots, "fx-plain")
    draft = discover(project, roots)
    assert all(rt.context is None for rt in draft.runtimes)
    cli = work_interface(draft)
    assert draft.runtime.id == cli.id and cli.invocable
    from app.connect.runtimes import context_standing

    assert context_standing(cli) == 0


# --------------------------------------------------------------------------- ranking

def profile(rid: str, creds: Credentials, context: ExecutionContext | None = None) -> RuntimeProfile:
    return RuntimeProfile(
        id=rid, kind=RuntimeKind.PYTHON_ENTRYPOINT, display_name="Runs from this project", availability="needs_worker", confidence="high",
        adapter={"kind": "command", "config": {"argv": ["python3", "-m", "x"]}}, credentials=creds, context=context,
    )


def a_context(**over) -> ExecutionContext:
    fields = {"kind": ContextKind.COMPOSE_RUN, "source_ref": "compose.yml:agent", "owner": "/srv/x", "program": ["python3", "-m", "x"],
              "input": ContextInput.ARGUMENT_DATA, "supplies": [KEY], "privilege": "same_user", "available": True, "matches": "native"}
    return ExecutionContext(**{**fields, **over})


def the_four():
    native = profile("native", Credentials(strategy=CredentialStrategy.PROJECT_DOTENV, names=[KEY], supplied=[KEY]))
    borrowed = profile("borrowed", Credentials(strategy=CredentialStrategy.DOCKER_ENVIRONMENT, names=[KEY], supplied=[KEY]), a_context())
    asked = profile("asked", Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=[KEY], required_from_user=True))
    unusable = profile("unusable", Credentials(strategy=CredentialStrategy.DOCKER_ENVIRONMENT, names=[KEY], supplied=[KEY]), a_context(problems=["needs_admin"]))
    return native, borrowed, asked, unusable


def test_the_order_is_native_then_the_provider_s_context_then_bevro_then_nothing(monkeypatch):
    """The ranking a context will have once it can be launched - checked now,
    by pretending one can, so that it is right before it is ever used."""
    monkeypatch.setattr(runtime_module, "LAUNCHABLE_CONTEXTS", frozenset({ContextKind.COMPOSE_RUN}))
    assert [rt.id for rt in rank(list(the_four()))] == ["native", "borrowed", "asked", "unusable"]
    native, borrowed, asked, _ = the_four()
    assert [rt.id for rt in rank([asked, borrowed])] == ["borrowed", "asked"]
    assert select([asked, borrowed])[0] == "borrowed"


def test_today_nothing_can_be_launched_so_a_context_is_never_chosen_or_counted():
    _native, borrowed, asked, _ = the_four()
    assert borrowed.context.compatible and not borrowed.context.launchable
    assert borrowed.invocable is False
    assert select([asked, borrowed])[0] == "asked"
    # Its credential is not an answer to the question the other way in asks.
    settled = settle_credentials([asked, borrowed])
    assert next(rt for rt in settled if rt.id == "asked").credentials.required_from_user is True


# --------------------------------------------------------------------------- trust

def test_approving_a_context_needs_its_project_and_names_exactly_one(seeded, tmp_path):
    from app.services import contexts as context_service
    from app.services import trust as trust_service

    project = tmp_path / "proj"
    project.mkdir()
    ctx = a_context(owner=str(project))
    assert context_service.authorisation(seeded, ctx) == "needs_approval"
    with pytest.raises(trust_service.TrustError):
        context_service.approve(seeded, ctx)  # the project itself isn't allowed yet

    trust_service.grant(seeded, trust_service.FOLDER, str(project))
    row = context_service.approve(seeded, ctx)
    seeded.commit()
    assert row.scope == "exact"
    assert context_service.authorisation(seeded, ctx) == "authorised"
    # Another service of the same project is another decision.
    assert context_service.authorisation(seeded, a_context(owner=str(project), source_ref="compose.yml:other")) == "needs_approval"
    # Settings shows what it is, not where it lives.
    shown = trust_service.public(row)
    assert shown["kind"] == "context" and str(project) not in str(shown)


def test_taking_back_either_grant_stops_the_context_being_allowed(seeded, tmp_path):
    from app.services import contexts as context_service
    from app.services import trust as trust_service

    project = tmp_path / "proj2"
    project.mkdir()
    folder = trust_service.grant(seeded, trust_service.FOLDER, str(project))
    ctx = a_context(owner=str(project))
    grant = context_service.approve(seeded, ctx)
    seeded.commit()
    trust_service.revoke(seeded, grant.id)
    seeded.commit()
    assert context_service.authorisation(seeded, ctx) == "needs_approval"

    context_service.approve(seeded, ctx)
    trust_service.revoke(seeded, folder.id)
    seeded.commit()
    assert context_service.authorisation(seeded, ctx) == "needs_approval"


def test_what_the_machine_forbids_no_grant_can_allow(seeded):
    from app.services import contexts as context_service

    assert context_service.authorisation(seeded, a_context(privilege="needs_admin")) == "not_permitted"


def test_a_malformed_context_key_is_not_granted(seeded):
    from app.services import trust as trust_service

    for key in ("", "folder:/x", "compose_run:agent", "compose_run:agent@relative", "nonsense:agent@/x"):
        with pytest.raises(trust_service.TrustError):
            trust_service.grant(seeded, trust_service.CONTEXT, key)


# --------------------------------------------------------------------------- programs

@pytest.mark.parametrize(
    ("argv", "fixed"),
    [
        (["python3", "-m", "pkg.agent"], True),
        (["/srv/.venv/bin/python", "-u", "-m", "pkg"], True),
        (["python3", "tool.py"], True),
        (["/usr/local/bin/agent-cli", "run"], True),
        (["/usr/bin/env", "FOO=1", "python3", "-m", "pkg"], True),
        (["python3"], False),
        (["python3", "-c", "print(1)"], False),
        (["node", "-e", "1"], False),
        (["sh", "-c", "anything"], False),
        (["/bin/bash", "script.sh"], False),
        ([], False),
    ],
)
def test_a_program_is_fixed_only_when_what_follows_it_is_data(argv, fixed):
    assert program_is_fixed(argv) is fixed


def test_the_same_program_means_the_same_interpreter_on_this_machine():
    assert same_program([PYTHON, "-m", "pkg", "run"], ["python3", "-m", "pkg"], host=True)
    assert not same_program(["/nowhere/else/python3", "-m", "pkg"], [PYTHON, "-m", "pkg"], host=True)
    assert not same_program([PYTHON, "-m", "pkg.other"], [PYTHON, "-m", "pkg"], host=True)
    # In a container only the family can be compared, and a script is the
    # same file wherever its path starts.
    assert same_program(["python", "/app/pkg/cli.py"], ["/home/x/.venv/bin/python", "pkg/cli.py"], host=False)
    assert not same_program(["node", "-m", "pkg"], ["python3", "-m", "pkg"], host=False)


# --------------------------------------------------------------------------- guards

def test_discovery_never_opens_a_credentials_file(roots, host, monkeypatch):
    project = socket_project(roots, "fx-guard-open")
    host.installed = {"fx-guard-open@.service": "system", "fx-guard-open.socket": "system"}
    held = str(roots.parent / "etc")
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        assert not str(file).startswith(held), f"opened {file}"
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    discover(project, roots)


def test_discovery_runs_nothing_that_starts_reads_or_inspects(roots, host):
    """No `systemctl start`, no `systemd-run`, no `docker` of any kind, no
    `sudo`: every command discovery runs is a question, never an action."""
    project = compose_project(roots, "fx-guard-run", "services:\n  agent:\n    build: .\n    entrypoint: [\"python3\", \"-m\", \"fx_guard_run\"]\n")
    socket = socket_project(roots, "fx-guard-run2")
    discover(project, roots)
    discover(socket, roots)
    for argv in host.commands:
        words = " ".join(argv)
        assert not re.search(r"\b(docker|sudo|systemd-run|pkexec)\b", words), words
        assert not re.search(r"systemctl.*\b(start|restart|enable|stop|set-environment|show-environment)\b", words), words


def test_no_code_reads_a_process_environment_or_inspects_a_container():
    app = Path(__file__).resolve().parents[1] / "app"
    adapters = Path(__file__).resolve().parents[2] / "adapters"
    offenders = []
    for path in [*app.rglob("*.py"), *adapters.glob("*.py")]:
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            if re.search(r"environ['\"]", code) and "/proc" in code:
                offenders.append(f"{path.name}: {line.strip()}")
            if re.search(r"['\"]inspect['\"]", code) and "docker" in code.lower():
                offenders.append(f"{path.name}: {line.strip()}")
            if re.search(r"-p['\"]?,?\s*['\"]Environment['\"]", code):
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], offenders


def test_no_production_code_names_a_real_project():
    app = Path(__file__).resolve().parents[1] / "app"
    adapters = Path(__file__).resolve().parents[2] / "adapters"
    pattern = re.compile(r"moimio|career[-_ ]?agent|/etc/[a-z]", re.IGNORECASE)
    # Where a web server image keeps its configuration *inside the container*:
    # matched against a Dockerfile's COPY targets, never opened on this machine.
    container_conventions = ('"/etc/nginx/"', '"/etc/caddy/"')

    def code(line: str) -> str:
        line = line.split("#", 1)[0]
        for convention in container_conventions:
            line = line.replace(convention, "")
        return line

    offenders = [f"{p.name}: {line.strip()}" for p in [*app.rglob("*.py"), *adapters.glob("*.py")] for line in p.read_text(encoding="utf-8").splitlines() if pattern.search(code(line)) and '"/etc"' not in line]
    assert offenders == [], offenders


def test_nothing_a_browser_sees_carries_a_secret_a_path_or_a_program(roots, host, seeded):
    from app.schemas.serialise import provider_details
    from app.services import providers as provider_service
    from app.services import runtime as runtime_service

    project = socket_project(roots, "fx-guard-shown")
    host.installed = {"fx-guard-shown@.service": "system", "fx-guard-shown.socket": "system"}
    draft = discover(project, roots)
    provider = provider_service.register_provider(
        seeded,
        {"name": "Shown", "description": "Writes briefs.", "capabilities": [{"id": "research", "title": "Research"}],
         "adapter": draft.runtime.adapter, "origin": "connected", "source": {"kind": "local", "target_kind": "local", "target": str(project)}},
    )
    runtime_service.set_runtimes(provider, draft.runtimes, draft.runtime.id)
    seeded.commit()

    details = provider_details(provider, seeded)
    assert details.contexts and details.contexts[0]["title"] == "System service that takes requests on a socket"
    assert details.contexts[0]["takes_work"].startswith("Could, in principle")
    for shown in (str(draft.public()), details.model_dump_json()):
        assert SECRET not in shown
        assert str(roots.parent / "etc") not in shown
        assert f"{PYTHON} -m" not in shown
