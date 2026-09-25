"""What a provider is now, as against what it once concluded.

Bevro learned several things separately, and each left a conclusion written
down at the time: this runtime is active, this credential is missing, this
connection is waiting. Conclusions go stale, and a page that shows several of
them at once starts contradicting itself - "waiting for the worker" beside
"reached from this machine: available", "uses its own credentials" beside "a
runtime that needs Bevro to supply one".

These tests are mostly about the contradictions being impossible rather than
merely absent: the same evidence, asked twice, cannot give two answers,
because there is only one place that answers.
"""

from __future__ import annotations

import pytest

from adapters.base import HealthResult
from adapters.runtime import CredentialStrategy, Credentials, RuntimeKind, RuntimeProfile
from app.services import providers as provider_service
from app.services import reconcile as reconcile_service
from app.services import runtime as runtime_service
from app.services import trust as trust_service

KEY = "OPENAI_API_KEY"


def runtime(rid: str, kind: RuntimeKind, *, creds: Credentials | None = None, invocable: bool = True, availability: str = "needs_worker") -> RuntimeProfile:
    adapter = {"kind": "command", "config": {"argv": ["python3", "-m", "x"], "cwd": "/tmp"}} if kind != RuntimeKind.HTTP else {"kind": "http", "config": {"base_url": "http://service.local"}}
    return RuntimeProfile(
        id=rid,
        kind=kind,
        display_name="Runs from this project" if kind != RuntimeKind.SYSTEMD else "Runs as a local service",
        adapter=adapter,
        credentials=creds or Credentials(),
        availability=availability,
        invocable=invocable,
        confidence="high",
    )


def a_provider(db, *runtimes: RuntimeProfile, origin: str = "connected"):
    provider = provider_service.register_provider(
        db,
        {"name": "Watcher", "description": "Watches things.", "capabilities": [{"id": "research", "title": "Research"}],
         "adapter": runtimes[0].adapter, "origin": origin, "source": {"kind": "local", "target_kind": "local", "target": "/tmp/watcher"}},
    )
    runtime_service.set_runtimes(provider, list(runtimes), runtimes[0].id)
    db.commit()
    return provider


def a_worker(db) -> None:
    provider_service.record_heartbeat(db, "somewhere:1", ["command", "claude_code"])


# --------------------------------------------------------------------------- the worker, said once

def test_work_happening_on_the_worker_is_not_the_same_as_waiting_for_one(seeded):
    """The contradiction from the screenshot. A local project always runs on
    the worker; that is not a reason to say it is waiting for one."""
    provider = a_provider(seeded, runtime("cli", RuntimeKind.CLI))
    a_worker(seeded)

    state = reconcile_service.state_of(seeded, provider)
    assert state.connection == "ready" and state.note is None
    assert reconcile_service.worker_state(seeded) == "running"


def test_waiting_for_the_worker_means_there_is_no_worker(seeded):
    provider = a_provider(seeded, runtime("cli", RuntimeKind.CLI))
    assert reconcile_service.worker_state(seeded) == "absent"
    state = reconcile_service.state_of(seeded, provider)
    assert state.connection == "waiting_for_worker"
    assert state.note == "Waiting for the worker on this machine"


def test_something_bevro_ships_that_nobody_set_up_is_not_a_fault(seeded):
    provider = a_provider(seeded, runtime("cli", RuntimeKind.CLI), origin="example")
    assert reconcile_service.state_of(seeded, provider).note == "Optional · needs the host worker"


def test_a_stale_worker_report_does_not_outlive_the_worker(seeded):
    """An old "unavailable" from a worker that has since recovered is not a
    statement about now."""
    provider = a_provider(seeded, runtime("cli", RuntimeKind.CLI))
    a_worker(seeded)
    provider_service.record_availability(seeded, provider, HealthResult(ok=False, state="unavailable", detail="was down"))
    assert reconcile_service.state_of(seeded, provider).connection == "unreachable"

    provider_service.record_availability(seeded, provider, HealthResult(ok=True, state="available"))
    assert reconcile_service.state_of(seeded, provider).connection == "ready"


# --------------------------------------------------------------------------- one selection, everywhere

def test_the_runtime_shown_is_the_runtime_that_would_be_used(seeded):
    """Discovery picked one; circumstances changed. What is shown has to
    follow the change, or the page is describing the past."""
    good = runtime("service", RuntimeKind.DOCKER_COMPOSE, creds=Credentials(names=[KEY], supplied=[KEY], strategy=CredentialStrategy.DOCKER_ENVIRONMENT), availability="ready")
    poor = runtime("cli", RuntimeKind.CLI, creds=Credentials(names=[KEY], strategy=CredentialStrategy.BEVRO_MANAGED, required_from_user=True))
    provider = a_provider(seeded, poor, good)          # stored as if the CLI had been chosen
    assert provider.active_runtime == "cli"

    state = reconcile_service.reconcile(seeded, provider)
    assert state.selected.id == "service"
    # ...and the record follows, so the execution engine and the page agree.
    seeded.refresh(provider)
    assert provider.active_runtime == "service"
    assert runtime_service.active_runtime(provider).id == "service"


def test_what_the_page_shows_and_what_would_run_cannot_disagree(seeded):
    a_worker(seeded)
    good = runtime("service", RuntimeKind.DOCKER_COMPOSE, creds=Credentials(names=[KEY], supplied=[KEY], strategy=CredentialStrategy.DOCKER_ENVIRONMENT), availability="ready")
    poor = runtime("cli", RuntimeKind.CLI, creds=Credentials(names=[KEY], strategy=CredentialStrategy.BEVRO_MANAGED, required_from_user=True))
    provider = a_provider(seeded, poor, good)

    shown = reconcile_service.state_of(seeded, provider).selected
    would_run, _skipped = runtime_service.eligible_runtimes(provider)
    assert shown.id == would_run[0].id


# --------------------------------------------------------------------------- credentials say one thing

def test_the_credential_summary_belongs_to_the_runtime_being_used(seeded):
    """"Uses its own" beside "managed by Bevro" is two answers to one
    question. There is one question."""
    from app.schemas.serialise import provider_details, provider_out

    a_worker(seeded)
    service = runtime("service", RuntimeKind.SYSTEMD, creds=Credentials(names=[KEY], supplied=[KEY], strategy=CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE, note="Uses credentials provided by the installed system service."), availability="ready")
    cli = runtime("cli", RuntimeKind.CLI, creds=Credentials(names=[KEY], strategy=CredentialStrategy.BEVRO_MANAGED, supplied_elsewhere=True))
    provider = a_provider(seeded, cli, service)
    reconcile_service.reconcile(seeded, provider)

    card = provider_out(provider, [], seeded)
    details = provider_details(provider, seeded)
    selected = next(r for r in details.runtimes if r["active"])

    assert card.runtime["display_name"] == selected["display_name"]
    assert "Bevro" not in card.runtime["credentials_label"]
    assert "provided by this way in" in selected["credential_summary"]


def test_a_way_in_that_cannot_get_a_credential_says_so_in_its_own_row(seeded):
    from app.schemas.serialise import provider_details

    a_worker(seeded)
    service = runtime("service", RuntimeKind.SYSTEMD, creds=Credentials(names=[KEY], supplied=[KEY], strategy=CredentialStrategy.SYSTEMD_ENVIRONMENT_FILE), availability="ready")
    cli = runtime("cli", RuntimeKind.CLI, creds=Credentials(names=[KEY], strategy=CredentialStrategy.BEVRO_MANAGED, supplied_elsewhere=True))
    provider = a_provider(seeded, cli, service)
    reconcile_service.reconcile(seeded, provider)

    rows = {r["id"]: r["credential_summary"] for r in provider_details(provider, seeded).runtimes}
    assert "another way in has it" in rows["cli"]
    # Never the strategy it happens to be built with, which says nothing true.
    assert "bevro managed" not in rows["cli"]


# --------------------------------------------------------------------------- nothing left over

def test_a_grant_taken_back_changes_what_a_provider_can_do(seeded, tmp_path):
    folder = tmp_path / "watcher"
    folder.mkdir()
    grant = trust_service.grant(seeded, trust_service.FOLDER, str(folder))
    seeded.commit()
    assert trust_service.allows_folder(seeded, str(folder)) is True

    trust_service.revoke(seeded, grant.id)
    assert trust_service.allows_folder(seeded, str(folder)) is False


def test_nothing_user_facing_mentions_the_variable_it_used_to_need(seeded):
    """Point five, enforced rather than checked once."""
    import re
    from pathlib import Path

    forbidden = re.compile(r"BEVRO_LOCAL_ROOTS|approved local folders|No local folders", re.IGNORECASE)
    app = Path(__file__).resolve().parents[1] / "app"
    allowed = {"services/trust.py", "models/trust.py"}  # where the boundary is implemented
    offenders = []
    for path in sorted(app.rglob("*.py")):
        rel = str(path.relative_to(app))
        if rel in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if forbidden.search(code):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert offenders == [], offenders


def test_a_provider_found_by_an_older_bevro_is_marked_for_another_look(seeded):
    provider = a_provider(seeded, runtime("cli", RuntimeKind.CLI))
    provider.discovery_version = 0
    seeded.commit()
    assert reconcile_service.needs_rediscovery(provider) is True

    provider.discovery_version = reconcile_service.DISCOVERY_VERSION
    seeded.commit()
    assert reconcile_service.needs_rediscovery(provider) is False


def test_something_that_cannot_be_looked_at_again_is_not_endlessly_retried(seeded):
    """A service Bevro was simply given an address for, or one of its own
    built-ins: there is nothing to rediscover, so it is left alone."""
    provider = a_provider(seeded, runtime("http", RuntimeKind.HTTP, availability="ready"), origin="example")
    provider.source = {}
    provider.discovery_version = 0
    seeded.commit()
    assert reconcile_service.needs_rediscovery(provider) is False
    reconcile_service.reconcile(seeded, provider)
    seeded.refresh(provider)
    assert provider.discovery_version == reconcile_service.DISCOVERY_VERSION
