"""Telling Bevro where something is, and being asked once.

The whole point of this file is that none of it knows what is being
connected. A folder is a folder: the person says where it is, Bevro says what
it would need permission for, they answer, and it carries on. The matrix is
the one from the brief, written against folders made in a temporary
directory, so nothing here depends on anything being installed.

    A  a folder nobody has mentioned before   -> asked
    B  allowed                                -> looked at
    C  the folder next door                   -> asked again
    D  a parent allowed deliberately          -> its children work
    E  a symlink pointing out of the folder   -> refused
    F  ..  and other ways up                  -> refused
    G  a program                              -> asked
    H  taking it back                         -> stops working
    I  an administrator's boundary            -> nothing outside it
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import get_settings
from app.services import trust as trust_service


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A machine with some folders on it and no configuration anywhere."""
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", "")
    monkeypatch.setenv("BEVRO_INTEGRATIONS_DIR", str(tmp_path / "bevro-integrations"))
    monkeypatch.setenv("BEVRO_AGENTS_DIR", str(tmp_path / "bevro-agents"))
    get_settings.cache_clear()
    for name in ("projects/alpha", "projects/beta", "elsewhere"):
        (tmp_path / name).mkdir(parents=True)
    yield tmp_path
    get_settings.cache_clear()


# --------------------------------------------------------------------------- A, B, C, D

def test_a_a_folder_nobody_has_mentioned_is_not_readable(seeded, home):
    assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha")) is False
    # ...and Bevro can still say what it would be agreeing to.
    facts = trust_service.look_at(str(home / "projects" / "alpha"))
    assert facts["exists"] and facts["is_directory"] and facts["reason"] is None
    assert facts["path"] == str(home / "projects" / "alpha") and facts["label"] == "alpha"


def test_b_allowing_it_is_enough(seeded, home):
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    seeded.commit()
    assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha")) is True
    # A project has subdirectories; allowing it allows working in it.
    (home / "projects" / "alpha" / "src").mkdir()
    assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha" / "src")) is True


def test_c_the_folder_next_door_is_a_separate_question(seeded, home):
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    seeded.commit()
    assert trust_service.allows_folder(seeded, str(home / "projects" / "beta")) is False
    assert trust_service.allows_folder(seeded, str(home / "projects")) is False


def test_d_a_parent_can_be_allowed_deliberately(seeded, home):
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects"), scope="tree")
    seeded.commit()
    for child in ("alpha", "beta"):
        assert trust_service.allows_folder(seeded, str(home / "projects" / child)) is True
    assert trust_service.allows_folder(seeded, str(home / "elsewhere")) is False


# --------------------------------------------------------------------------- E, F

def test_e_a_shortcut_out_of_an_allowed_folder_leads_nowhere(seeded, home):
    """Symlinks are followed before anything is allowed or checked, so a link
    added afterwards grants nothing it points at."""
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    seeded.commit()
    (home / "projects" / "alpha" / "escape").symlink_to(home / "elsewhere")
    assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha" / "escape")) is False
    # ...and the link cannot be granted as a way of granting its destination.
    (home / "elsewhere" / "secret").mkdir()
    assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha" / "escape" / "secret")) is False


def test_f_ways_of_walking_up_out_of_a_folder_are_refused(seeded, home):
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    seeded.commit()
    for climb in ("..", "../beta", "../../elsewhere", "./../beta"):
        assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha" / climb)) is False
    with pytest.raises(trust_service.TrustError):
        trust_service.canonical("relative/path")


@pytest.mark.parametrize("forbidden", ["/etc", "/etc/ssl", "/proc", "/", "/home", "/usr/bin"])
def test_the_machine_s_own_workings_are_never_granted(forbidden):
    if not Path(forbidden).exists():
        pytest.skip(f"{forbidden} is not on this machine")
    assert trust_service.refuse_reason(Path(forbidden)) is not None


def test_a_folder_of_credentials_is_not_a_project(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    assert trust_service.refuse_reason(tmp_path / ".ssh") is not None


# --------------------------------------------------------------------------- G

def test_g_a_program_is_asked_about_by_name_and_never_by_its_arguments(seeded, home):
    facts = trust_service.look_at_command(["python3", "-m", "thing", "--key", "hunter2"])
    assert facts["label"] == "python3" and facts["exists"] is True
    assert "hunter2" not in str(facts)

    assert trust_service.allows_command(seeded, ["python3", "-m", "thing"]) is False
    trust_service.grant(seeded, trust_service.COMMAND, "python3")
    seeded.commit()
    assert trust_service.allows_command(seeded, ["python3", "-m", "anything", "--key", "hunter2"]) is True
    assert trust_service.allows_command(seeded, ["node", "index.js"]) is False


# --------------------------------------------------------------------------- H

def test_h_taking_it_back_takes_effect_without_a_restart(seeded, home):
    row = trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    seeded.commit()
    assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha")) is True

    trust_service.revoke(seeded, row.id)
    assert trust_service.allows_folder(seeded, str(home / "projects" / "alpha")) is False
    # The record is kept: what was allowed, and when it stopped being.
    assert seeded.get(type(row), row.id).revoked_at is not None


def test_what_a_process_may_touch_follows_what_is_written_down(seeded, home):
    from adapters import localroots

    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    seeded.commit()
    trust_service.apply_to_process(seeded)
    assert localroots.resolve_within(str(home / "projects" / "alpha")) == home / "projects" / "alpha"
    with pytest.raises(localroots.OutsideRoots):
        localroots.resolve_within(str(home / "projects" / "beta"))


# --------------------------------------------------------------------------- I

def test_i_an_administrator_s_boundary_cannot_be_exceeded(seeded, home, monkeypatch):
    """A shared installation can still be pinned. Inside the boundary the
    person decides; outside it, nobody does."""
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(home / "projects"))
    get_settings.cache_clear()

    assert trust_service.inside_ceiling(home / "projects" / "alpha") is True
    assert trust_service.inside_ceiling(home / "elsewhere") is False
    with pytest.raises(trust_service.TrustError):
        trust_service.grant(seeded, trust_service.FOLDER, str(home / "elsewhere"))

    # ...and a folder the administrator named needs no further asking.
    assert trust_service.allows_folder(seeded, str(home / "projects" / "beta")) is True


def test_a_grant_made_before_a_boundary_was_set_stops_counting(seeded, home, monkeypatch):
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "elsewhere"))
    seeded.commit()
    assert trust_service.allows_folder(seeded, str(home / "elsewhere")) is True

    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(home / "projects"))
    get_settings.cache_clear()
    assert trust_service.allows_folder(seeded, str(home / "elsewhere")) is False


# --------------------------------------------------------------------------- what Settings shows

def test_settings_shows_the_folder_but_never_a_command_s_arguments(seeded, home):
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    trust_service.grant(seeded, trust_service.COMMAND, "/usr/bin/python3")
    seeded.commit()
    shown = [trust_service.public(row) for row in trust_service.active_grants(seeded)]
    folder = next(g for g in shown if g["kind"] == "folder")
    program = next(g for g in shown if g["kind"] == "command")
    assert folder["target"] == str(home / "projects" / "alpha")
    assert program["target"] == "python3"  # the name, not the path it was found at
    assert all(g["revoked_at"] is None for g in shown)


def test_asking_twice_about_the_same_folder_is_one_permission(seeded, home):
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    trust_service.grant(seeded, trust_service.FOLDER, str(home / "projects" / "alpha"))
    seeded.commit()
    assert len(trust_service.active_grants(seeded, trust_service.FOLDER)) == 1


# --------------------------------------------------------------------------- through Connect, as a person would

def a_worker_is_running(db, monkeypatch) -> None:
    from app.services import providers as provider_service

    monkeypatch.setattr(provider_service, "worker_seen_recently", lambda _db: True)


def worker_looks(db) -> int:
    """One pass of what the worker does, in this process."""
    from app.services import connect as connect_service

    return connect_service.run_pending(db, [])


def test_a_folder_is_asked_about_once_and_then_just_works(client, seeded, home, monkeypatch):
    """The whole point, end to end: give Bevro a path, answer one question,
    and discovery carries on by itself."""
    from tests.connect_fixtures import make_python_project

    project = make_python_project(home / "projects", name="alpha-agent")
    a_worker_is_running(seeded, monkeypatch)

    # 1. The person types where it is. Nothing has been looked at yet.
    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    assert body["state"] == "looking"

    # 2. The worker, which is the process that can see the filesystem, asks.
    worker_looks(seeded)
    body = client.get(f"/api/connect/drafts/{body['id']}").json()
    assert body["state"] == "trust_required"
    assert body["trust"]["path"] == str(project) and body["trust"]["kind"] == "folder"
    assert body["trust"]["label"] == "alpha-agent"

    # 3. They say yes, once, about that one folder.
    body = client.post(f"/api/connect/drafts/{body['id']}/trust", json={"scope": "exact"}).json()
    assert body["state"] == "looking" and body["trust"] is None

    # 4. And it carries on by itself.
    worker_looks(seeded)
    body = client.get(f"/api/connect/drafts/{body['id']}").json()
    assert body["state"] == "found", body.get("error")
    assert body["draft"]["name"]

    # 5. The permission outlives the conversation, and is there to take back.
    grants = client.get("/api/trust").json()["grants"]
    assert [g["target"] for g in grants] == [str(project)]
    assert grants[0]["kind"] == "folder" and grants[0]["granted_by"] == "person"


def test_the_folder_next_door_is_asked_about_separately(client, seeded, home, monkeypatch):
    from tests.connect_fixtures import make_python_project

    first = make_python_project(home / "projects", name="alpha-agent")
    second = make_python_project(home / "projects", name="beta-agent")
    a_worker_is_running(seeded, monkeypatch)

    body = client.post("/api/connect/discover", json={"target": str(first)}).json()
    worker_looks(seeded)
    client.post(f"/api/connect/drafts/{body['id']}/trust", json={"scope": "exact"})
    worker_looks(seeded)

    body = client.post("/api/connect/discover", json={"target": str(second)}).json()
    worker_looks(seeded)
    assert client.get(f"/api/connect/drafts/{body['id']}").json()["state"] == "trust_required"


def test_allowing_the_parent_covers_what_is_in_it(client, seeded, home, monkeypatch):
    from tests.connect_fixtures import make_python_project

    first = make_python_project(home / "projects", name="alpha-agent")
    second = make_python_project(home / "projects", name="beta-agent")
    a_worker_is_running(seeded, monkeypatch)

    body = client.post("/api/connect/discover", json={"target": str(first)}).json()
    worker_looks(seeded)
    asked = client.get(f"/api/connect/drafts/{body['id']}").json()["trust"]
    assert asked["parent_label"] == "projects"
    client.post(f"/api/connect/drafts/{body['id']}/trust", json={"scope": "parent"})

    body = client.post("/api/connect/discover", json={"target": str(second)}).json()
    worker_looks(seeded)
    assert client.get(f"/api/connect/drafts/{body['id']}").json()["state"] == "found"


def test_taking_a_permission_back_stops_the_next_look(client, seeded, home, monkeypatch):
    from tests.connect_fixtures import make_python_project

    project = make_python_project(home / "projects", name="alpha-agent")
    a_worker_is_running(seeded, monkeypatch)
    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    worker_looks(seeded)
    client.post(f"/api/connect/drafts/{body['id']}/trust", json={"scope": "exact"})
    worker_looks(seeded)

    grant_id = client.get("/api/trust").json()["grants"][0]["id"]
    assert client.delete(f"/api/trust/{grant_id}").status_code == 204

    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    worker_looks(seeded)
    assert client.get(f"/api/connect/drafts/{body['id']}").json()["state"] == "trust_required"


def test_something_that_is_not_there_is_not_a_question_about_permission(client, seeded, home, monkeypatch):
    a_worker_is_running(seeded, monkeypatch)
    body = client.post("/api/connect/discover", json={"target": str(home / "nothing-here")}).json()
    worker_looks(seeded)
    body = client.get(f"/api/connect/drafts/{body['id']}").json()
    assert body["state"] == "failed" and "nothing at that path" in body["error"]


def test_an_address_is_never_asked_about(client, seeded, monkeypatch):
    """A network service is reached, not opened. Permission is about this
    machine, and nothing on the network is on this machine."""
    from tests import operational_fixtures as fx
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy

    transport, _calls = fx.service(descriptor=fx.JOBS_API)
    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(transport), LocalProjectStrategy(), CommandStrategy()], use_assist=False))
    try:
        for address in ("http://127.0.0.1:8080/", "http://10.0.0.8:8080/", "http://100.64.0.3:6300/p/someone/", "https://agent.example.com/"):
            body = client.post("/api/connect/discover", json={"target": address}).json()
            assert body["state"] == "found", (address, body.get("error"))
            assert body["trust"] is None
    finally:
        set_discovery_service(None)
