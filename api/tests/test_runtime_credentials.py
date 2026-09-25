"""Whose credential is it, and can *this* way in actually get it?

A project can have more than one way in, and they do not have the same
answer. Its installed service is handed an environment by the system; its
command line, run by the worker as whoever runs the worker, may have nothing
at all. Three things that look identical from one runtime and are not:

    this way in cannot get it        say so, on that runtime
    nothing here has it              ask the person, once
    another way in already has it    say nothing, and prefer that way in

Bevro never opens a credentials file. A unit file is configuration and may be
read; what it points at is not. A file that cannot even be stat'd is the
strongest evidence there is that something is looking after it properly, and
is treated as such.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.runtime import CredentialStrategy, Credentials, RuntimeKind
from app.connect.probes import EnvironmentFileRef, SystemdUnit, _environment_names, _file_state
from app.connect.runtimes import rank, settle_credentials
from app.connect.strategies.local import _systemd_credentials
from tests.connect_fixtures import make_serviced_project

# Whatever the project asks for. The name is the project's business - this
# one asks for the same thing the real case does, so nothing is special-cased
# into passing.
KEY = "OPENAI_API_KEY"


def unit(**over) -> SystemdUnit:
    fields = {"name": "fixture.service", "unit_type": "oneshot", "installed": True, "active": False}
    return SystemdUnit(**{**fields, **over})


def file_ref(state: str, *, optional: bool = False) -> EnvironmentFileRef:
    return EnvironmentFileRef(path="/etc/fixture/credentials", state=state, optional=optional)


# --------------------------------------------------------------------------- what a unit supplies

def test_a_unit_that_loads_a_file_supplies_what_the_project_needs():
    creds = _systemd_credentials(unit(environment_files=[file_ref("present")]), [KEY])
    assert creds.strategy == CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE
    assert creds.supplied == [KEY] and creds.missing == []
    assert creds.status == "configured" and creds.owner == "runtime"
    assert creds.required_from_user is False


def test_a_credentials_file_bevro_may_not_even_look_at_still_counts():
    """The case the whole thing is for: root-owned, mode 600, and Bevro
    cannot stat it. That is not absence - it is the file being looked after."""
    creds = _systemd_credentials(unit(environment_files=[file_ref("protected")]), [KEY])
    assert creds.status == "configured" and creds.strategy == CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE


def test_a_unit_that_sets_the_variable_itself_supplies_it_by_name():
    creds = _systemd_credentials(unit(environment_names=[KEY, "PYTHONUNBUFFERED"]), [KEY])
    assert creds.strategy == CredentialStrategy.SYSTEMD_ENVIRONMENT
    assert creds.supplied == [KEY] and creds.status == "configured"


def test_a_unit_that_sets_other_variables_supplies_nothing_of_the_sort():
    creds = _systemd_credentials(unit(environment_names=["PYTHONUNBUFFERED", "TZ"]), [KEY])
    assert creds.supplied == [] and creds.status == "incomplete"


def test_a_reference_to_a_file_that_is_not_there_is_not_a_credential():
    """Point nine: the reference disappearing makes the runtime incomplete,
    and something else has to supply it or somebody has to be asked."""
    creds = _systemd_credentials(unit(environment_files=[file_ref("missing")]), [KEY])
    assert creds.status == "incomplete"
    assert creds.strategy == CredentialStrategy.UNKNOWN
    assert "isn't there" in (creds.note or "")


def test_a_file_the_unit_marked_optional_says_nothing_either_way():
    creds = _systemd_credentials(unit(environment_files=[file_ref("missing", optional=True)]), [KEY])
    assert creds.status == "incomplete" and creds.strategy == CredentialStrategy.RUNTIME_MANAGED


def test_a_unit_with_nothing_to_supply_and_nothing_needed_asks_nothing():
    assert _systemd_credentials(unit(), []).status == "none"


# --------------------------------------------------------------------------- never the contents

def test_only_names_are_taken_from_a_unit_and_never_values():
    assert _environment_names(f"{KEY}=super-secret-value") == [KEY]
    assert _environment_names('"A=one" B=two') == ["A", "B"]
    creds = _systemd_credentials(unit(environment_names=[KEY]), [KEY])
    assert "super-secret-value" not in str(creds.model_dump())


def test_a_file_is_described_by_stat_alone_and_never_opened(tmp_path, monkeypatch):
    secret = tmp_path / "credentials"
    secret.write_text("FIXTURE_UPSTREAM_KEY=must-never-be-read\n", encoding="utf-8")
    opened: list[str] = []
    real_open = Path.open

    def watched(self, *a, **kw):
        opened.append(str(self))
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", watched)
    assert _file_state(secret) == "present"
    creds = _systemd_credentials(unit(environment_files=[EnvironmentFileRef(path=str(secret), state="present")]), [KEY])
    assert creds.status == "configured"
    assert str(secret) not in opened
    assert "must-never-be-read" not in str(creds.model_dump())


# --------------------------------------------------------------------------- across the ways in

def profile(rid: str, kind: RuntimeKind, creds: Credentials, *, invocable: bool = True):
    """One way in, with nothing to tell it apart but its kind and its keys."""
    from adapters.runtime import RuntimeProfile

    adapter = (
        {"kind": "command", "config": {"argv": ["python3", "-m", "x"], "cwd": "/tmp"}}
        if kind == RuntimeKind.CLI
        else {"kind": "http", "config": {"base_url": "http://localhost:9"}}
    )
    return RuntimeProfile(
        id=rid,
        kind=kind,
        display_name="Runs from this project" if kind == RuntimeKind.CLI else "Runs as a local service",
        adapter=adapter,
        credentials=creds,
        availability="ready",
        confidence="high",
        invocable=invocable,
    )


def test_one_way_in_having_it_means_nobody_is_asked_for_it():
    """The middle case. The command line still cannot get it, and still says
    so - but the person is not asked for something the machine already has."""
    cli = profile("cli", RuntimeKind.CLI, Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=[KEY], required_from_user=True))
    service = profile("systemd", RuntimeKind.SYSTEMD, Credentials(strategy=CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE, names=[KEY], supplied=[KEY]))

    settle_credentials([cli, service])
    assert cli.credentials.required_from_user is False
    assert cli.credentials.status == "incomplete"  # honest: this way in still cannot
    assert service.credentials.status == "configured"


def test_nobody_having_it_is_still_a_question_for_the_person():
    cli = profile("cli", RuntimeKind.CLI, Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=[KEY], required_from_user=True))
    service = profile("systemd", RuntimeKind.SYSTEMD, Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, names=[KEY]))

    settle_credentials([cli, service])
    assert cli.credentials.required_from_user is True


def test_the_way_in_that_has_what_it_needs_is_preferred():
    """Point five, with both ways in able to take work."""
    cli = profile("cli", RuntimeKind.CLI, Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=[KEY], required_from_user=True))
    service = profile("compose", RuntimeKind.DOCKER_COMPOSE, Credentials(strategy=CredentialStrategy.DOCKER_ENVIRONMENT, names=[KEY], supplied=[KEY]))

    ordered = rank(settle_credentials([cli, service]))
    assert ordered[0].id == "compose"


def test_a_complete_way_in_beats_an_incomplete_one_of_the_same_kind():
    """Nothing else to tell them apart: the credentials decide."""
    poor = profile("a", RuntimeKind.DOCKER_COMPOSE, Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED, names=[KEY]))
    good = profile("b", RuntimeKind.DOCKER_COMPOSE, Credentials(strategy=CredentialStrategy.DOCKER_ENVIRONMENT, names=[KEY], supplied=[KEY]))
    assert rank([poor, good])[0].id == "b"


def test_credentials_are_a_fact_about_one_way_in_not_about_the_project():
    cli = profile("cli", RuntimeKind.CLI, Credentials(names=[KEY], strategy=CredentialStrategy.BEVRO_MANAGED, required_from_user=True))
    service = profile("systemd", RuntimeKind.SYSTEMD, Credentials(names=[KEY], supplied=[KEY], strategy=CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE))
    settle_credentials([cli, service])
    assert [rt.credentials.status for rt in (cli, service)] == ["incomplete", "configured"]
    # Not Bevro's - it has nothing to give - and not this runtime's either.
    assert [rt.credentials.owner for rt in (cli, service)] == ["other_runtime", "runtime"]


# --------------------------------------------------------------------------- a whole project, discovered

def discover(project: Path, roots: Path, monkeypatch):
    """Discovery over one project, with nothing in the environment."""
    from app.connect.strategies.base import DiscoveryContext
    from app.connect.strategies.local import LocalProjectStrategy
    from app.connect.targets import classify_target

    monkeypatch.delenv(KEY, raising=False)
    return LocalProjectStrategy().discover(classify_target(str(project)), DiscoveryContext(roots=[roots]))


@pytest.fixture
def roots(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    from app.config import get_settings

    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def test_a_scheduled_service_s_credential_is_recognised_and_still_out_of_reach(roots, monkeypatch):
    """The shape from the real case: a command line with nothing, and an
    installed unit handed its credentials by the system.

    Bevro recognises the unit's credential source exactly. What it must not
    do is conclude that *it* can therefore run the project: the unit is a
    scheduled job, nothing can be handed to it, and a key it holds is a key
    Bevro cannot use. So the question stands - and the answer explains what
    was found rather than pretending nothing was.
    """
    project = make_serviced_project(roots)
    draft = discover(project, roots, monkeypatch)

    service = next(rt for rt in draft.runtimes if rt.kind == "systemd")
    assert service.credentials.strategy == "systemd_environment_file"
    assert service.credentials.status == "configured" and service.credentials.owner == "runtime"
    assert service.invocable is False  # a timer job cannot be given work

    cli = next(rt for rt in draft.runtimes if rt.kind in ("cli", "python_entrypoint"))
    assert cli.credentials.status == "incomplete"
    assert draft.auth.required is True
    assert "installed system service has its own" in (cli.credentials.note or "")
    assert "Bevro needs its own OPENAI_API_KEY" in (cli.credentials.note or "")
    assert "fixture-secret-never-read" not in str(draft.model_dump())


def test_a_service_that_can_take_work_settles_the_question_for_everyone(roots, monkeypatch):
    """The other half of the same rule. A Compose service holds its own
    credentials *and* can be handed work, so nobody is asked for anything."""
    from tests.connect_fixtures import make_managed_project

    project = make_managed_project(roots, port=59999, name="fixture-composed", with_cli=True)
    draft = discover(project, roots, monkeypatch)
    supplier = next((rt for rt in draft.runtimes if rt.credentials.status == "configured" and rt.invocable), None)
    assert supplier is not None, [(rt.kind, rt.credentials.status, rt.invocable) for rt in draft.runtimes]
    assert draft.auth.required is False


def test_a_unit_setting_the_variable_itself_is_read_the_same_way(roots, monkeypatch):
    """Environment= rather than EnvironmentFile=, recognised as precisely -
    and, being the same kind of scheduled job, just as out of reach."""
    project = make_serviced_project(roots, name="fixture-inline", credentials_file=False, inline_environment=True)
    draft = discover(project, roots, monkeypatch)
    service = next(rt for rt in draft.runtimes if rt.kind == "systemd")
    assert service.credentials.strategy == "systemd_environment"
    assert service.credentials.status == "configured"
    assert "fixture-secret-never-read" not in str(draft.model_dump())


def test_a_project_with_no_service_at_all_is_still_asked_about(roots, monkeypatch):
    """The other case, which must not be swept up with it: nothing here has
    the key, so somebody has to be asked."""
    project = make_serviced_project(roots, name="fixture-bare")
    (project / "deploy" / "fixture-bare.service").unlink()
    draft = discover(project, roots, monkeypatch)
    assert not any(rt.kind == "systemd" for rt in draft.runtimes)
    assert draft.auth.required is True and draft.auth.secret_name == KEY


def test_a_unit_pointing_at_a_file_that_is_gone_supplies_nothing(roots, monkeypatch):
    """Point nine: the reference disappears, and the question comes back."""
    project = make_serviced_project(roots, name="fixture-dangling", credentials_file=False, environment_file="/etc/fixture-dangling-nowhere/credentials")
    draft = discover(project, roots, monkeypatch)
    service = next(rt for rt in draft.runtimes if rt.kind == "systemd")
    assert service.credentials.status == "incomplete"
    assert draft.auth.required is True


def test_a_project_that_carries_its_own_env_needs_no_service_and_no_asking(roots, monkeypatch):
    project = make_serviced_project(roots, name="fixture-dotenv", credentials_file=False, dotenv=True)
    draft = discover(project, roots, monkeypatch)
    cli = next(rt for rt in draft.runtimes if rt.kind in ("cli", "python_entrypoint"))
    assert cli.credentials.strategy == "project_dotenv" and cli.credentials.status == "configured"
    assert draft.auth.required is False
    assert "fixture-secret-never-read" not in str(draft.model_dump())


def test_no_secret_value_reaches_the_browser_by_any_of_these_routes(roots, monkeypatch):
    for name, kwargs in (
        ("fixture-a", {}),
        ("fixture-b", {"credentials_file": False, "inline_environment": True}),
        ("fixture-c", {"credentials_file": False, "dotenv": True}),
    ):
        project = make_serviced_project(roots, name=name, **kwargs)
        draft = discover(project, roots, monkeypatch)
        shown = str(draft.public())
        assert "fixture-secret-never-read" not in shown
        assert "/etc/" not in shown and str(project) not in shown
