"""What Connect tells the page, so that every state has one obvious next step.

The page decides the words and the buttons; these are the facts it decides
them from: what a test actually checked, what kind of failure happened,
whether this is already connected, and what the person said it is for.
"""

import textwrap

import pytest

from app.config import get_settings
from app.connect.service import set_discovery_service
from app.models import ConnectDraft
from app.schemas.providers import HealthOut
from app.services import connect as connect_service
from tests.connect_fixtures import make_python_project, snapshot

SELF_TESTING_AGENT = textwrap.dedent(
    '''
    """An agent with a check of its own."""
    import argparse
    import sys


    def main() -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--topic")
        parser.add_argument("--self-test", action="store_true")
        args = parser.parse_args()
        if args.self_test:
            print("configuration looks fine")
            sys.exit(0)
        print("Working on:", args.topic)


    if __name__ == "__main__":
        main()
    '''
)

DRY_RUNNING_AGENT = SELF_TESTING_AGENT.replace('"--self-test"', '"--dry-run"').replace("args.self_test", "args.dry_run")


@pytest.fixture(autouse=True)
def reset_discovery():
    yield
    set_discovery_service(None)


@pytest.fixture
def local_roots(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.setenv("BEVRO_INTEGRATIONS_DIR", str(tmp_path / "integrations"))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _project(root, source):
    project = make_python_project(root)
    (project / "src" / "fixture_research" / "agent.py").write_text(source, encoding="utf-8")
    return project


# ----------------------------------------------------------------------------- test

def test_a_programs_own_self_check_is_found_and_is_what_test_runs(client, seeded, local_roots, monkeypatch):
    project = _project(local_roots, SELF_TESTING_AGENT)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-on-this-machine")  # the machine has it, so nothing is missing
    before = snapshot(project)
    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    assert body["state"] == "found"
    test = client.post(f"/api/connect/drafts/{body['id']}/test", json={}).json()["test"]
    assert test["ok"] is True and test["detail"] == "Everything checked, including its own self-check."
    assert test["checks"][-1] == {"label": "Its own self-check passed", "ok": True, "kind": "functional"}
    assert "configuration looks fine" not in str(test)  # what it printed stays on the server
    assert snapshot(project) == before


def test_a_dry_run_is_not_taken_for_a_self_check(client, seeded, local_roots, monkeypatch):
    project = _project(local_roots, DRY_RUNNING_AGENT)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-on-this-machine")
    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    test = client.post(f"/api/connect/drafts/{body['id']}/test", json={}).json()["test"]
    # Everything that can be checked without running it passed - and it says
    # plainly that no real task ran, rather than calling that a full pass.
    assert test["ok"] is True and "without running a real task" in test["detail"]
    assert test["checks"][-1] == {"label": "It doesn't offer a self-check, so no real task was run", "ok": None, "kind": "functional"}


def test_a_missing_program_stops_the_list_where_it_fails(client, seeded, local_roots):
    project = make_python_project(local_roots)
    body = client.post("/api/connect/discover", json={"target": str(project)}).json()
    (project / ".venv" / "bin" / "python").unlink()
    test = client.post(f"/api/connect/drafts/{body['id']}/test", json={}).json()["test"]
    assert test["ok"] is False and test["next"] is None  # a credential would not fix this
    assert [(c["label"], c["ok"]) for c in test["checks"]] == [("Found it on this machine", True), ("Its Python environment is missing", False)]


# ----------------------------------------------------------------------------- failures

def test_failures_say_what_kind_they_are(seeded):
    worker = ConnectDraft(target_kind="local", target="/x", state="failed", error=connect_service.WORKER_NEEDED_MESSAGE)
    timeout = ConnectDraft(target_kind="local", target="/x", state="failed", error=connect_service.WORKER_TIMEOUT_MESSAGE)
    named = ConnectDraft(target_kind="name", target="nope", named="nope", state="failed", error=connect_service.NOT_FOUND_BY_NAME.format(name="nope"))
    unreachable = ConnectDraft(target_kind="url", target="http://x", state="failed", error="Couldn't reach it.", unreachable=True)
    other = ConnectDraft(target_kind="url", target="http://x", state="failed", error="Something else.")
    found = ConnectDraft(target_kind="url", target="http://x", state="found")
    assert [connect_service.problem_of(r) for r in (worker, timeout, named, unreachable, other, found)] == ["worker", "worker", "not_found", "unreachable", "failed", None]


# ----------------------------------------------------------------------------- already connected

def test_something_already_connected_is_pointed_at_not_offered_again(client, seeded, local_roots):
    project = make_python_project(local_roots)
    first = client.post("/api/connect/discover", json={"target": str(project)}).json()
    assert first["already_connected"] is None
    provider = client.post(f"/api/connect/drafts/{first['id']}/confirm", json={"secrets": {"OPENAI_API_KEY": "sk-fixture"}}).json()
    again = client.post("/api/connect/discover", json={"target": str(project)}).json()
    assert again["state"] == "found" and again["already_connected"] == {"id": provider["id"], "name": provider["name"]}


# ----------------------------------------------------------------------------- manage

def test_manage_test_keeps_reachable_and_needs_a_credential_apart(seeded):
    from app.routers.providers import _with_checks
    from app.services import providers as provider_service

    provider = provider_service.register_provider(
        seeded,
        {
            "name": "Keyed",
            "description": "",
            "capabilities": [{"id": "research", "title": "Research"}],
            "adapter": {"kind": "command", "config": {"argv": ["python", "-m", "x"], "secret_env": ["OPENAI_API_KEY"], "input": {"mode": "argument"}}},
            "origin": "connected",
        },
    )
    out = _with_checks(provider, HealthOut(ok=True, detail="Runs on this machine."), [])
    assert out.ok is False and out.next == "add_credential"
    assert out.detail == "It can be reached, but it needs a credential before it can run."
    assert [(c["label"], c["ok"]) for c in out.checks] == [
        ("Bevro can reach it", True),
        ("No OpenAI credential yet for tasks Bevro starts", False),
        ("No real task was run", None),
    ]
    out = _with_checks(provider, HealthOut(ok=True, detail=None), ["OPENAI_API_KEY"])
    assert out.ok is True and out.next is None and ("Has the credentials it needs", True) in [(c["label"], c["ok"]) for c in out.checks]
