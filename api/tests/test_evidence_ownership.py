"""Every fact Bevro holds about an app is that app's own, and "direct use
needs a credential" is said only when it is true.

- A running service is asked where it actually listens, not on 127.0.0.1:
  a service bound to one address (a private-network one, say) is otherwise
  missed, and Bevro falls back to guessing.
- What a running service declares it can do beats words picked out of its
  README.
- A systemd unit or timer found by the name of a file a project ships is
  that project's only if what systemd runs is inside the project: a name is
  not an identity.
- A missing credential is the answer only when it blocks every way in that
  could take work, and names what is missing when Bevro can tell.
- Credentials Bevro holds are held for one item; they never satisfy another.

Synthetic projects and providers only.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import httpx
import pytest

from adapters.runtime import Credentials, CredentialStrategy, RuntimeHealth, RuntimeKind
from app.connect import probes
from app.connect import surfaces as surface
from app.connect.inspect import Project
from app.connect.runtimes import cli_runtime, http_runtime
from app.connect.strategies import local
from app.connect.strategies.base import DiscoveryContext
from app.connect.strategies.python import inspect_python
from app.models import ProviderSecret
from app.routing.resolve import resolve
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.services.reconcile import direct_access, state_of
from tests import operational_fixtures as fx


# --------------------------------------------------------------------------- where a running service listens

def test_the_address_a_socket_is_bound_to_is_read_correctly():
    # /proc/net/tcp{,6} store each 32-bit word little-endian.
    assert probes._address("0100007F") == "127.0.0.1"
    assert probes._address("5F395764") == "100.87.57.95"
    assert probes._address("00000000") == "0.0.0.0"
    assert probes._address("00000000000000000000000000000000") == "::"
    assert probes._address("0000000000000000FFFF00000100007F") == "127.0.0.1"  # IPv4 as IPv6
    assert probes._address("zz") is None


def _web_project(root: Path, name: str, readme: str) -> Path:
    project = root / name
    (project / name.replace("-", "_")).mkdir(parents=True)
    (project / "pyproject.toml").write_text(f'[project]\nname = "{name}"\ndependencies = ["fastapi", "uvicorn"]\n', encoding="utf-8")
    (project / "README.md").write_text(readme, encoding="utf-8")
    (project / name.replace("-", "_") / "__init__.py").write_text("", encoding="utf-8")
    return project


def _answering_only_at(host: str):
    """A service that answers where it listens and nowhere else, like a real one."""
    inner, _paths = fx.service(descriptor=fx.JOBS_API)
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.host}:{request.url.port}")
        if request.url.host != host:
            raise httpx.ConnectError("connection refused", request=request)
        return inner.handle_request(request)

    return httpx.MockTransport(handler), asked


def test_a_service_bound_to_one_address_is_found_there_with_what_it_says_it_can_do(tmp_path, monkeypatch):
    project_dir = _web_project(tmp_path, "fixture-desk", "# Fixture Desk\n\nSearch, research, drafts, quotes and orders for your product strategy.\n")
    monkeypatch.setattr(local, "listening_processes", lambda root: [probes.RunningProcess(pid=1, program="python", ports=[6300], addresses={6300: "10.9.8.7"})])
    transport, asked = _answering_only_at("10.9.8.7")
    project = Project(project_dir)
    draft = local.compose_draft(project, [inspect_python(project)], [], DiscoveryContext(roots=[tmp_path], transport=transport, timeout=3.0), probe_host=True)

    assert asked and all(a.startswith("10.9.8.7:6300") for a in asked)  # never 127.0.0.1
    [running] = [rt for rt in draft.runtimes if rt.kind == RuntimeKind.PROCESS]
    assert running.adapter["config"]["base_url"] == "http://10.9.8.7:6300"
    # No framework-default guess beside the real thing.
    assert not any("localhost:8000" in str(rt.adapter) for rt in draft.runtimes)
    # What it declares, not what its README's words happen to match.
    ids = {c.id for c in draft.capabilities}
    assert "quotes" not in ids and "product_strategy" not in ids
    assert ids and ids <= {c.id for c in _jobs_api_capabilities()}


def _jobs_api_capabilities():
    transport, _ = fx.service(descriptor=fx.JOBS_API)
    return local._discover_url("http://service.local", DiscoveryContext(transport=transport, timeout=3.0)).capabilities


# --------------------------------------------------------------------------- A, B: one project's surfaces stay its own

def _scheduled_chat_project(root: Path) -> Path:
    project = root / "fixture-digest"
    (project / "fixture_digest").mkdir(parents=True)
    (project / "deploy").mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "fixture-digest"\ndependencies = ["requests"]\n', encoding="utf-8")
    (project / "README.md").write_text("# Fixture Digest\n\nSends a weekly digest to Telegram.\n", encoding="utf-8")
    (project / "fixture_digest" / "__init__.py").write_text("", encoding="utf-8")
    (project / "fixture_digest" / "__main__.py").write_text(
        "import os, requests\nTOKEN = os.environ['TELEGRAM_BOT_TOKEN']\nrequests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', json={})\n", encoding="utf-8"
    )
    (project / "deploy" / "fixture-digest.timer").write_text("[Timer]\nOnCalendar=Mon *-*-* 07:30:00 UTC\n", encoding="utf-8")
    return project


def _discover(project_dir: Path, **kw):
    project = Project(project_dir)
    findings = [f for f in (inspect_python(project),) if f is not None]
    return local.compose_draft(project, findings, [], DiscoveryContext(roots=[project_dir.parent], timeout=3.0, **kw), probe_host=False)


def test_A_B_two_projects_looked_at_one_after_the_other_keep_their_own_surfaces(tmp_path):
    digest = _discover(_scheduled_chat_project(tmp_path))
    desk = _discover(_web_project(tmp_path, "fixture-desk", "# Fixture Desk\n\nAnswers questions about jobs.\n"))
    kinds = lambda d: {(s.kind, s.role) for s in d.surfaces}  # noqa: E731
    assert ("schedule", "runs") in kinds(digest) and ("telegram", "delivers") in kinds(digest)
    assert not {k for k, _ in kinds(desk)} & {"schedule", "telegram"}
    # Looking at the first again after the second changes nothing about either.
    assert kinds(_discover(tmp_path / "fixture-digest")) == kinds(digest)


def test_M_sending_results_to_a_chat_is_not_a_way_to_use_it():
    delivers = [surface.Surface(kind="telegram", role="delivers")]
    assert not surface.has_a_way_to_use(delivers)


# --------------------------------------------------------------------------- a name is not an identity

def test_an_installed_unit_is_a_projects_only_if_it_runs_from_the_project(tmp_path):
    root = tmp_path / "fixture-web"
    mine = probes.SystemdUnit(name="web.service", working_directory=str(root), exec_start=f"{root}/.venv/bin/python -m web")
    via_program = probes.SystemdUnit(name="web.service", exec_start=f"-{root}/bin/start --port 1")
    elsewhere = probes.SystemdUnit(name="web.service", working_directory="/srv/another-app", exec_start="/srv/another-app/bin/start")
    sibling = probes.SystemdUnit(name="web.service", working_directory=f"{root}-old")
    assert probes._points_into(mine, root) and probes._points_into(via_program, root)
    assert not probes._points_into(elsewhere, root) and not probes._points_into(sibling, root)


def test_a_unit_installed_under_the_same_name_for_something_else_lends_this_project_nothing(tmp_path, monkeypatch):
    project_dir = tmp_path / "fixture-web"
    (project_dir / "deploy").mkdir(parents=True)
    (project_dir / "deploy" / "web.service").write_text(f"[Service]\nWorkingDirectory={project_dir}\nExecStart={project_dir}/.venv/bin/python -m web\n", encoding="utf-8")
    (project_dir / "deploy" / "web.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
    other = tmp_path / "installed"
    other.mkdir()
    (other / "web.service").write_text("[Service]\nWorkingDirectory=/srv/another-app\nExecStart=/srv/another-app/run\nEnvironmentFile=/etc/another-app/secrets\n", encoding="utf-8")
    (other / "web.timer").write_text("[Timer]\nOnCalendar=hourly\n", encoding="utf-8")
    monkeypatch.setattr(probes, "_unit_state", lambda name: (True, True, "system"))
    monkeypatch.setattr(probes, "_unit_files", lambda name, scope: [str(other / name)])
    [unit] = probes.systemd_units(Project(project_dir))
    assert unit.installed is False and unit.active is None
    assert unit.exec_start == f"{project_dir}/.venv/bin/python -m web" and unit.environment_files == []
    [timer] = probes.systemd_timers(Project(project_dir))
    assert timer.installed is False

    # And when what is installed is this project's, it is believed.
    (other / "web.service").write_text(f"[Service]\nWorkingDirectory={project_dir}\nExecStart={project_dir}/.venv/bin/python -m web --port 9\n", encoding="utf-8")
    [unit] = probes.systemd_units(Project(project_dir))
    assert unit.installed is True and unit.exec_start.endswith("--port 9")
    [timer] = probes.systemd_timers(Project(project_dir))
    assert timer.installed is True


# --------------------------------------------------------------------------- C, D, E, F, N: does it need a credential?

def _http(rid="api", *, down=False, needs_start=False):
    rt = http_runtime(
        rid,
        kind=RuntimeKind.HTTP,
        adapter={"kind": "http", "config": {"base_url": f"http://{rid}.invalid", "invoke": {"method": "POST", "path": "/task", "body": {"prompt": "{request}"}}, "response": {"text": "answer"}}},
        display_name="Connected over the network",
        availability="needs_start" if needs_start else "ready",
        confidence="high",
        credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, names=["OPENAI_API_KEY"]),
        evidence=[],
    )
    if down:
        rt = rt.model_copy(update={"health": RuntimeHealth().after_failure("refused")})
    return rt


def _cli(rid="cli", *, name="OPENAI_API_KEY", elsewhere=False):
    return cli_runtime(
        rid,
        kind=RuntimeKind.CLI,
        adapter={"kind": "command", "config": {"argv": ["./agent"], "cwd": "/tmp", "input": {"mode": "argument"}, "output": {"modes": ["stdout"]}, "secret_env": [name]}},
        display_name="Runs from this project",
        availability="ready",
        confidence="high",
        credentials=Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=[name], required_from_user=True, supplied_elsewhere=elsewhere, held_for="its scheduled runs" if elsewhere else None),
        evidence=[],
    )


def _provider(db, name, runtimes, *, surfaces=(), capabilities=({"id": "research", "title": "Research"},)):
    p = provider_service.register_provider(
        db,
        {"name": name, "capabilities": [dict(c) for c in capabilities], "adapter": runtimes[0].adapter if runtimes else {}, "runtimes": list(runtimes), "active_runtime": runtimes[0].id if runtimes else None, "surfaces": list(surfaces), "origin": "connected"},
    )
    db.commit()
    return p


def test_C_D_a_credential_free_way_in_is_used_whatever_another_way_needs(db):
    a = _provider(db, "Fixture Brief", [_cli()])
    b = _provider(db, "Fixture Desk", [_http(), _cli()])
    assert direct_access(db, a)["state"] == "needs_credential"
    assert direct_access(db, b)["state"] == "ready"
    assert state_of(db, b).selected.id == "api"


def test_a_credential_free_way_in_that_is_down_is_the_problem_not_the_credential(db):
    down = _provider(db, "Fixture Desk", [_http(down=True), _cli()])
    stopped = _provider(db, "Fixture Board", [_http(down=True, needs_start=True), _cli()])
    assert direct_access(db, down)["state"] == "unreachable"
    assert direct_access(db, stopped)["state"] == "needs_start"
    assert "credential" not in direct_access(db, down)["note"]


def test_E_only_credentialed_ways_in_need_a_credential_and_say_which(db):
    p = _provider(db, "Fixture Brief", [_cli(), _cli("cli-2")])
    access = direct_access(db, p)
    assert access == {"state": "needs_credential", "needs": "OpenAI API key", "note": "Bevro needs an OpenAI API key before it can send it work."}
    unknown = _provider(db, "Fixture Other", [_cli(name="WIDGET_API_KEY")])
    assert direct_access(db, unknown)["needs"] == "Widget API key"
    answer = resolve(db, "Ask Fixture Brief to research widgets")
    assert answer.message == "Fixture Brief can do this, but direct use in Bevro needs an OpenAI API key."


def test_F_a_web_only_app_has_no_credential_to_need(db):
    web = surface.web_surface("http://127.0.0.1:6400", bind="all", title="Notebook")
    p = _provider(db, "Fixture Notebook", [], surfaces=[web])
    access = direct_access(db, p)
    assert access["state"] == "not_set_up" and "needs" not in access and "credential" not in access["note"]


def test_N_a_schedule_that_gets_its_key_elsewhere_does_not_give_bevro_the_key(db):
    p = _provider(db, "Fixture Brief", [_cli(elsewhere=True)])
    assert direct_access(db, p)["state"] == "needs_credential"


def test_K_L_a_credential_bevro_holds_is_held_for_one_item_only(db):
    a = _provider(db, "Fixture Brief", [_cli()])
    b = _provider(db, "Fixture Board", [_cli()])
    db.add(ProviderSecret(provider_id=a.id, name="OPENAI_API_KEY", ciphertext=b"not-a-real-secret"))
    db.commit()
    db.refresh(a)
    db.refresh(b)
    assert direct_access(db, a)["state"] != "needs_credential"
    assert direct_access(db, b)["state"] == "needs_credential" and direct_access(db, b)["needs"] == "OpenAI API key"


# --------------------------------------------------------------------------- I, J, O

def test_I_what_the_person_says_it_is_for_makes_no_way_in(db):
    p = _provider(db, "Fixture Pantry", [], capabilities=[{"id": "meal_planning", "title": "Meal planning", "by": "person"}])
    assert p.surfaces == [] and direct_access(db, p)["state"] == "not_set_up"
    assert resolve(db, "Plan my meals").outcome == "setup"


def test_J_asking_again_gives_the_same_answer(db):
    p = _provider(db, "Fixture Desk", [_http(down=True), _cli()])
    first = (direct_access(db, p), [s for s in p.surfaces])
    db.expire_all()
    p = provider_service.get_provider(db, p.id)
    assert (direct_access(db, p), [s for s in p.surfaces]) == first


def test_O_the_status_the_test_and_the_work_all_use_the_same_way_in(db, monkeypatch):
    p = _provider(db, "Fixture Desk", [_cli(), _http()])  # listed CLI first; the HTTP way needs nothing
    chosen = state_of(db, p).selected.id
    tried: list[str] = []

    def invoke_once(provider, runtime, request, context):
        from adapters import InvocationResult, ResultState

        tried.append(runtime.id)
        return InvocationResult(state=ResultState.COMPLETED, summary="ok"), []

    checked: list[str] = []
    monkeypatch.setattr(runtime_service, "_invoke_once", invoke_once)
    monkeypatch.setattr(runtime_service, "check_runtime", lambda provider, runtime, secrets: checked.append(runtime.id) or fx_health_ok())
    from adapters import InvocationRequest

    runtime_service.execute(p, InvocationRequest(task_id="t", run_id="r", request="x"))
    runtime_service.health(p, {})
    assert chosen == "api" and tried == ["api"] and checked == ["api"]


def fx_health_ok():
    from adapters import HealthResult

    return HealthResult(ok=True)


# --------------------------------------------------------------------------- G, H: looking again

def test_G_H_looking_again_replaces_what_was_found_and_keeps_what_the_person_said(seeded):
    from app.services import connect as connect_service
    from tests.test_execution_location import _connected_service, _discovery_over

    provider = _connected_service(seeded, fx.service(descriptor=fx.JOBS_API)[0])
    # Stale technical evidence from an earlier look, and the person's own words.
    provider.surfaces = [surface.Surface(kind="schedule", role="runs", when="every Monday").model_dump(mode="json", exclude_none=True), surface.Surface(kind="telegram", role="delivers").model_dump(mode="json", exclude_none=True)]
    provider.capabilities = [{"id": "household_admin", "title": "Household admin", "by": "person"}, *provider.capabilities]
    seeded.commit()
    _discovery_over(fx.service(descriptor=fx.JOBS_API)[0])
    connect_service.reconnect_provider(seeded, provider)
    seeded.refresh(provider)
    assert not {s.get("kind") for s in provider.surfaces} & {"schedule", "telegram"}
    assert provider.capabilities[0]["by"] == "person"
    # And the person's words made no way in of their own.
    assert all(rt.id != "household_admin" for rt in runtime_service.runtimes_of(provider))


# --------------------------------------------------------------------------- which one is this for?

PROFILES = [{"profile_id": "alex", "display_name": "Alex Morgan"}, {"profile_id": "sam", "display_name": "Sam Lee"}]


def test_a_running_api_behind_a_folder_asks_which_profile_like_one_connected_by_address(tmp_path, monkeypatch):
    project_dir = _web_project(tmp_path, "fixture-desk", "# Fixture Desk\n\nAnswers questions.\n")
    monkeypatch.setattr(local, "listening_processes", lambda root: [probes.RunningProcess(pid=1, program="python", ports=[6300], addresses={6300: "10.9.8.7"})])
    inner, _ = fx.service(descriptor=fx.scoped_api(PROFILES), spa=True, profiles=PROFILES)
    transport = httpx.MockTransport(lambda r: inner.handle_request(r) if r.url.host == "10.9.8.7" else (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)))
    project = Project(project_dir)
    draft = local.compose_draft(project, [inspect_python(project)], [], DiscoveryContext(roots=[tmp_path], transport=transport, timeout=3.0), probe_host=True)
    assert draft.scope_choices == [{"value": "alex", "label": "Alex Morgan"}, {"value": "sam", "label": "Sam Lee"}]


def _pending_scope_item(db):
    api_way = http_runtime(
        "running", kind=RuntimeKind.HTTP, adapter={"kind": "openapi", "config": fx.as_config(fx.scoped_api(PROFILES))}, display_name="Already running on this machine",
        availability="not_invocable", confidence="high", credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED), evidence=[], accepts_prompt=False,
    )
    p = _provider(db, "Fixture Desk", [_cli(), api_way])
    p.source = {"kind": "local", "target": "/nowhere", "scope_choices": [{"value": "alex", "label": "Alex Morgan"}, {"value": "sam", "label": "Sam Lee"}]}
    db.commit()
    return p


def test_an_item_whose_api_needs_a_choice_says_so_rather_than_asking_for_a_key(db):
    p = _pending_scope_item(db)
    access = direct_access(db, p)
    assert access["state"] == "needs_choice" and "needs" not in access
    assert access["choices"] == [{"value": "alex", "label": "Alex Morgan"}, {"value": "sam", "label": "Sam Lee"}]
    answer = resolve(db, "Ask Fixture Desk to review my cases")
    assert answer.outcome == "choose" and answer.message == "Fixture Desk can do this once you say which one it's for."


def test_answering_which_one_makes_the_api_the_way_in_and_is_kept(db):
    from app.services import connect as connect_service

    p = _pending_scope_item(db)
    with pytest.raises(connect_service.DraftError):
        connect_service.choose_scope(db, p, "nobody")
    connect_service.choose_scope(db, p, "sam")
    assert direct_access(db, p)["state"] == "ready"
    selected = state_of(db, p).selected
    assert selected.id == "running" and selected.adapter["config"]["context"] == {"profile_id": "sam"}
    assert p.source["connection_context"] == {"profile_id": "sam"} and "scope_choices" not in p.source


def test_looking_again_applies_the_answer_already_given(seeded):
    from app.services import connect as connect_service
    from tests.test_execution_location import _discovery_over

    _discovery_over(fx.service(descriptor=fx.scoped_api(PROFILES), spa=True, profiles=PROFILES)[0])
    row = connect_service.start_discovery(seeded, "http://service.local/")
    provider = connect_service.confirm_draft(seeded, row, name=None, description=None, capability_summary=None, secrets={}, app_url=None, scope="sam")
    assert direct_access(seeded, provider)["state"] == "ready"
    _discovery_over(fx.service(descriptor=fx.scoped_api(PROFILES), spa=True, profiles=PROFILES)[0])
    connect_service.reconnect_provider(seeded, provider)
    seeded.refresh(provider)
    assert direct_access(seeded, provider)["state"] == "ready"
    assert state_of(seeded, provider).selected.adapter["config"]["context"] == {"profile_id": "sam"}
    assert "scope_choices" not in (provider.source or {})


def test_a_running_api_that_is_also_a_page_for_people_is_both(tmp_path, monkeypatch):
    project_dir = _web_project(tmp_path, "fixture-desk", "# Fixture Desk\n\nAnswers questions.\n")
    monkeypatch.setattr(local, "listening_processes", lambda root: [probes.RunningProcess(pid=1, program="python", ports=[6300], addresses={6300: "10.9.8.7"})])
    inner, _ = fx.service(descriptor=fx.JOBS_API, spa=True)
    transport = httpx.MockTransport(lambda r: inner.handle_request(r) if r.url.host == "10.9.8.7" else (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)))
    project = Project(project_dir)
    draft = local.compose_draft(project, [inspect_python(project)], [], DiscoveryContext(roots=[tmp_path], transport=transport, timeout=3.0), probe_host=True)
    [web] = [s for s in draft.surfaces if s.kind == "web_app"]
    assert web.role == "use" and web.url.startswith("http://10.9.8.7:6300")
    assert any(rt.kind == RuntimeKind.PROCESS and rt.invocable for rt in draft.runtimes)
