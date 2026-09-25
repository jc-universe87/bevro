"""Connect by name: "tell Bevro what it is called".

A person who knows what a project is called should not also have to know
where it lives. The worker turns a name into the folder it names - reading
directory names and nothing else - and hands that folder to exactly the
flow a typed path goes through: the same permission question, the same
discovery. These tests hold that to account:

    A  a unique folder, by its name                    found, then asked about
    B  by the words of its name                        the same folder
    C  a name that fits two folders                    asked which, not guessed
    D  an address                                      no search at all
    E  a full path                                     no search, as before
    F  a ~/ path                                       no search, as before
    G  a folder not yet allowed                        nothing inside read first
    H  a folder already allowed                        straight to discovery
    I  a matching folder somewhere protected           never offered
    J  a link that leads out                           never offered
    K  dependency and cache trees                      never searched
    L  nothing by that name                            said plainly
    M  a project that needs a credential               still needs it

Every test runs in a home directory of its own: the real one is never
searched.
"""

from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest

from app.connect import names
from app.connect.targets import classify_target, stored_target
from app.services import connect as connect_service
from app.services import providers as provider_service
from app.services import trust as trust_service

KEY = "OPENAI_API_KEY"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory with nothing configured, and a worker that is running."""
    home = tmp_path / "home" / "someone"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", "")
    monkeypatch.setenv("BEVRO_INTEGRATIONS_DIR", str(tmp_path / "bevro-integrations"))
    monkeypatch.setenv("BEVRO_AGENTS_DIR", str(tmp_path / "bevro-agents"))
    monkeypatch.delenv(KEY, raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


def project(where: Path, *, deps: tuple[str, ...] = ("openai",)) -> Path:
    """A Python project: a folder whose name is what it is called."""
    where.mkdir(parents=True)
    package = where.name.replace("-", "_").lower()
    (where / "pyproject.toml").write_text(f'[project]\nname = "{where.name}"\ndescription = "Writes briefs"\ndependencies = [{", ".join(repr(d).replace(chr(39), chr(34)) for d in deps)}]\n', encoding="utf-8")
    (where / "README.md").write_text(f"# {where.name}\n\nWrites briefs about widgets.\n", encoding="utf-8")
    (where / package).mkdir()
    (where / package / "__init__.py").write_text("", encoding="utf-8")
    (where / package / "__main__.py").write_text('import argparse\np = argparse.ArgumentParser()\np.add_argument("--prompt")\nprint(p.parse_args().prompt)\n', encoding="utf-8")
    return where


def connect(db, text: str):
    """Submit Connect, then let the worker do what it does."""
    provider_service.record_heartbeat(db, "somewhere:1", ["command"])
    row = connect_service.start_discovery(db, text)
    connect_service.run_pending(db, [])
    db.refresh(row)
    return row


def searched(monkeypatch) -> list[str]:
    """Record every name search, still doing it."""
    calls: list[str] = []
    real = names.find_named

    def watched(name, *args, **kwargs):
        calls.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(names, "find_named", watched)
    return calls


# --------------------------------------------------------------------------- A, B

def test_a_a_unique_folder_is_found_by_its_name_and_then_asked_about(seeded, home):
    folder = project(home / "agents" / "research-agent")
    row = connect(seeded, "research-agent")
    assert row.state == "trust_required"
    assert row.trust["path"] == str(folder.resolve())
    assert row.target_kind == "local" and row.named == "research-agent"


@pytest.mark.parametrize("typed", ["Research Agent", "research_agent", "RESEARCH-AGENT", "research agent"])
def test_b_the_words_of_a_name_find_the_same_folder(seeded, home, typed):
    folder = project(home / "agents" / "research-agent")
    row = connect(seeded, typed)
    assert row.state == "trust_required" and row.trust["path"] == str(folder.resolve())


def test_b_a_project_s_own_package_is_not_a_second_match(seeded, home):
    """research-agent/src/research_agent is the project, not another one."""
    folder = project(home / "agents" / "research-agent")
    (folder / "src" / "research_agent").mkdir(parents=True)
    row = connect(seeded, "research-agent")
    assert row.state == "trust_required" and row.trust["path"] == str(folder.resolve())


# --------------------------------------------------------------------------- C

def test_c_a_name_that_fits_two_folders_is_a_question_not_a_guess(seeded, home):
    project(home / "agents" / "research-agent")
    project(home / "archive" / "research_agent")
    row = connect(seeded, "research-agent")
    assert row.state == "choice_required"
    assert row.trust is None and row.draft is None  # nothing asked about, nothing looked at
    shown = connect_service.choices_public(row)
    assert shown == [
        {"label": "research-agent", "where": "~/agents/research-agent"},
        {"label": "research_agent", "where": "~/archive/research_agent"},
    ]
    # The person picks; then the ordinary question, about that folder only.
    connect_service.choose_candidate(seeded, row, 1)
    connect_service.run_pending(seeded, [])
    seeded.refresh(row)
    assert row.state == "trust_required"
    assert row.trust["path"] == str((home / "archive" / "research_agent").resolve())


def test_c_only_a_folder_that_was_found_can_be_chosen(seeded, home):
    project(home / "agents" / "research-agent")
    project(home / "archive" / "research-agent")
    row = connect(seeded, "research-agent")
    with pytest.raises(connect_service.DraftError):
        connect_service.choose_candidate(seeded, row, 7)


def test_c_the_browser_chooses_by_position_and_sees_no_more_than_it_needs(client, db, home):
    project(home / "agents" / "research-agent")
    project(home / "archive" / "research-agent")
    provider_service.record_heartbeat(db, "somewhere:1", ["command"])
    started = client.post("/api/connect/discover", json={"target": "Research Agent"}).json()
    connect_service.run_pending(db, [])
    draft = client.get(f"/api/connect/drafts/{started['id']}").json()
    assert draft["state"] == "choice_required"
    assert [c["where"] for c in draft["choices"]] == ["~/agents/research-agent", "~/archive/research-agent"]
    assert str(home) not in str(draft)
    chosen = client.post(f"/api/connect/drafts/{started['id']}/choose", json={"choice": 0}).json()
    assert chosen["state"] == "looking" and chosen["choices"] is None


# --------------------------------------------------------------------------- D, E, F

def test_d_an_address_is_never_searched_for(seeded, home, monkeypatch):
    calls = searched(monkeypatch)
    connect(seeded, "http://127.0.0.1:1/")
    assert classify_target("http://127.0.0.1:1/").kind == "url" and calls == []


def test_e_a_full_path_is_used_exactly_as_before(seeded, home, monkeypatch):
    folder = project(home / "agents" / "research-agent")
    calls = searched(monkeypatch)
    row = connect(seeded, str(folder))
    assert calls == [] and row.named is None
    assert row.state == "trust_required" and row.trust["path"] == str(folder.resolve())


def test_f_a_home_relative_path_is_used_exactly_as_before(seeded, home, monkeypatch):
    folder = project(home / "agents" / "research-agent")
    calls = searched(monkeypatch)
    row = connect(seeded, "~/agents/research-agent")
    assert calls == [] and row.state == "trust_required" and row.trust["path"] == str(folder.resolve())


# --------------------------------------------------------------------------- G, H

def test_g_nothing_inside_is_read_before_the_person_says_yes(seeded, home, monkeypatch):
    folder = project(home / "agents" / "research-agent")
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        assert not str(file).startswith(str(folder)), f"read {file} before being allowed to"
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    row = connect(seeded, "research-agent")
    assert row.state == "trust_required"
    assert trust_service.allows_folder(seeded, str(folder)) is False  # finding it granted nothing

    monkeypatch.setattr(builtins, "open", real_open)
    connect_service.grant_for_draft(seeded, row)
    connect_service.run_pending(seeded, [])
    seeded.refresh(row)
    assert row.state == "found"


def test_h_a_folder_already_allowed_goes_straight_to_discovery(seeded, home):
    folder = project(home / "agents" / "research-agent")
    trust_service.grant(seeded, trust_service.FOLDER, str(folder))
    seeded.commit()
    row = connect(seeded, "research-agent")
    assert row.state == "found" and row.draft["name"]


def test_h_folders_near_ones_already_allowed_are_found_even_outside_home(seeded, home, tmp_path):
    """Where the person already works is looked at first - wherever it is."""
    elsewhere = tmp_path / "work" / "agents"
    project(elsewhere / "report-writer")
    trust_service.grant(seeded, trust_service.FOLDER, str(elsewhere))
    seeded.commit()
    row = connect(seeded, "Report Writer")
    assert row.state == "found"


# --------------------------------------------------------------------------- I, J, K

def test_i_a_matching_folder_somewhere_protected_is_never_offered(seeded, home):
    for private in (".ssh", ".aws", ".gnupg", ".config/gcloud"):
        (home / private / "research-agent").mkdir(parents=True)
    row = connect(seeded, "research-agent")
    assert row.state == "failed" and "couldn't find anything called" in row.error


def test_i_outside_an_administrator_s_boundary_is_never_offered(seeded, home, monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    project(home / "agents" / "research-agent")  # outside the boundary
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(allowed))
    from app.config import get_settings

    get_settings.cache_clear()
    row = connect(seeded, "research-agent")
    assert row.state == "failed"


def test_j_a_link_that_leads_out_of_the_search_is_not_followed_or_offered(seeded, home, tmp_path):
    outside = project(tmp_path / "someone-else" / "research-agent")
    (home / "agents").mkdir()
    (home / "agents" / "research-agent").symlink_to(outside)
    (home / "agents" / "system").symlink_to("/etc")
    row = connect(seeded, "research-agent")
    assert row.state == "failed"


def test_j_a_link_inside_home_is_offered_as_where_it_really_is(seeded, home):
    real = project(home / "code" / "research-agent")
    (home / "links").mkdir()
    (home / "links" / "research-agent").symlink_to(real)
    row = connect(seeded, "research-agent")
    # The same folder twice is one folder.
    assert row.state == "trust_required" and row.trust["path"] == str(real.resolve())


def test_k_dependency_and_cache_trees_are_never_searched(seeded, home):
    for tree in ("app/node_modules", "app/.venv/lib", "app/venv", "app/__pycache__", "app/.git", ".cache", ".local/share/Trash"):
        (home / tree / "research-agent").mkdir(parents=True)
    row = connect(seeded, "research-agent")
    assert row.state == "failed"


def test_k_depth_network_mounts_and_budget(home):
    """Five levels below home is further than anyone keeps a project."""
    deep = home / "a" / "b" / "c" / "d" / "research-agent"
    deep.mkdir(parents=True)
    area = [names.SearchArea(home, 4)]
    assert names.find_named("research-agent", area, refuse=lambda p: None) == []
    assert names.find_named("research-agent", [names.SearchArea(home, 5)], refuse=lambda p: None) == [deep.resolve()]
    # Someone else's disk is not walked.
    (home / "mnt" / "research-agent").mkdir(parents=True)
    assert names.find_named("research-agent", [names.SearchArea(home, 3)], refuse=lambda p: None, mounts=frozenset({str(home / "mnt")})) == []
    # A search that has seen enough stops.
    assert names.find_named("research-agent", [names.SearchArea(home, 5)], refuse=lambda p: None, max_directories=3) == []


def test_k_a_search_reads_names_and_never_a_file(home, monkeypatch):
    project(home / "agents" / "research-agent")
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        assert not str(file).startswith(str(home)), f"opened {file}"
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    assert names.find_named("research-agent", [names.SearchArea(home, 4)], refuse=trust_service.refuse_reason)


# --------------------------------------------------------------------------- L

def test_l_nothing_by_that_name_is_said_plainly(seeded, home):
    row = connect(seeded, "research-agent")
    assert row.state == "failed"
    assert row.error == "Bevro couldn't find anything called “research-agent” on this machine. If you know where it is, paste the folder's full path instead."


# --------------------------------------------------------------------------- M

def test_m_a_project_found_by_name_needs_what_it_needed_before(seeded, home, tmp_path):
    """Finding a project by name changes where it was found, not what it needs.

    The same shape as a real project with an installed service that has the
    key and cannot be handed work: the command line still needs its own.
    """
    folder = project(home / "agents" / "research-agent")
    held = tmp_path / "etc" / "research-agent"
    held.mkdir(parents=True)
    (held / "credentials").write_text(f"{KEY}=fixture-secret-never-read\n", encoding="utf-8")
    (folder / "deploy").mkdir()
    (folder / "deploy" / "research-agent.service").write_text(
        f"[Service]\nType=oneshot\nEnvironmentFile={held / 'credentials'}\nWorkingDirectory={folder}\nExecStart=/usr/bin/python3 -m research_agent run\n",
        encoding="utf-8",
    )
    trust_service.grant(seeded, trust_service.FOLDER, str(folder))
    seeded.commit()
    by_name = connect(seeded, "research-agent")
    by_path = connect(seeded, str(folder))
    assert by_name.state == by_path.state == "found"
    assert by_name.draft["auth"]["required"] is True and by_name.draft["auth"]["secret_name"] == KEY
    for key in ("name", "description", "capabilities", "auth"):
        assert by_name.draft[key] == by_path.draft[key]
    assert "fixture-secret-never-read" not in str(by_name.draft)


# --------------------------------------------------------------------------- what it was before

def test_plain_words_that_are_a_program_on_this_machine_are_still_a_command(seeded, home, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "fixture-tool"
    tool.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    calls = searched(monkeypatch)
    row = connect(seeded, "fixture-tool serve")
    assert row.target_kind == "command" and calls == []
    assert row.state == "trust_required" and row.trust["kind"] == "command"


def test_what_was_connected_is_read_back_as_what_it_was():
    """A command connected before names existed stays a command; a bare name
    under an allowed folder is still looked for there."""
    assert stored_target("command", "fixture-tool serve").kind == "command"
    assert stored_target("local", "/srv/agents/x").kind == "local"
    assert stored_target("local", "my-agent").kind == "name"


def test_an_older_provider_connected_by_a_bare_name_can_still_be_looked_at_again(home, tmp_path):
    from app.connect.strategies.base import DiscoveryContext
    from app.connect.strategies.local import LocalProjectStrategy

    allowed = tmp_path / "allowed"
    project(allowed / "my-agent")
    draft = LocalProjectStrategy(probe_host=False).discover(stored_target("local", "my-agent"), DiscoveryContext(roots=[allowed]))
    assert draft.runtime is not None
