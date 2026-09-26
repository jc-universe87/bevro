"""A workspace is its owner's: it starts empty, and it can be emptied again.

Nothing here is about demos. It is about what a real installation contains on
the first day, what "remove this" means, and what must survive it.
"""

from __future__ import annotations

import uuid

import pytest

from adapters import InvocationResult, ResultState
from app.automations.schedule import Recurrence, ScheduleSpec
from app.models import Artifact, Automation, AutomationRun, NotificationEvent, Provider, ProviderRun, Task
from app.services import automations as automation_service
from app.services import notifications as notification_service
from app.services import providers as provider_service
from app.services import tasks as task_service


# --------------------------------------------------------------------------- a clean sheet

def test_a_fresh_installation_has_no_agents_of_its_own(client, db):
    """What someone sees on the first day: nothing pretending to be theirs."""
    provider_service.seed_examples(db, demo=False)
    # Nothing: Apps & agents is what the person connected or created.
    assert client.get("/api/providers").json() == []
    # What Bevro could add is offered, not added.
    assert [o["slug"] for o in client.get("/api/integrations").json()] == ["claude-code"]

    assert client.get("/api/tasks").json() == []
    assert client.get("/api/automations").json() == []
    assert client.get("/api/notifications").json() == {"unread": 0, "items": []}


def test_asking_for_work_with_nothing_connected_says_so_plainly(client, db):
    provider_service.seed_examples(db, demo=False)
    response = client.post("/api/tasks", json={"request": "Compare three note-taking apps"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["reason"] == "no_provider"
    assert detail["message"] == "I don't have an app or agent that looks suited to this yet."
    assert client.get("/api/tasks").json() == []  # nothing half-made was left behind


def test_both_routers_cope_with_an_empty_workspace(db):
    """Neither router may invent a provider, and the model is never even asked."""
    from app.routing.deterministic import DeterministicRouter
    from app.routing.llm import LLMRouter
    from tests.test_routing import FakeModel

    provider_service.seed_examples(db, demo=False)
    assert DeterministicRouter().route(db, "Compare three note-taking apps").selected_provider_ids == []

    model = FakeModel()
    decision = LLMRouter(model=model).route(db, "Compare three note-taking apps")
    assert decision.selected_provider_ids == []
    assert model.calls == []  # nothing was sent anywhere


def test_create_still_previews_with_nothing_connected(client, db):
    """Describing an agent works; building one needs a builder, and says so."""
    provider_service.seed_examples(db, demo=False)
    preview = client.post("/api/create/preview", json={"description": "Watch competitor pricing pages weekly"}).json()
    assert preview["name"] and preview["can"]  # it still describes what it would be
    assert preview["can_build"] is False  # ...but says it cannot be built yet


# --------------------------------------------------------------------------- removing one piece of work

@pytest.fixture
def research(seeded):
    return provider_service.get_by_slug(seeded, "research")


def finished_task(db, provider, request="Compare three note-taking apps") -> Task:
    task = task_service.submit(db, request, provider=provider)
    db.commit()
    task_service.execute_run(db, task.runs[-1].id)
    db.commit()
    return task


def test_removing_a_task_takes_its_work_and_leaves_the_agent(client, seeded, research):
    task = finished_task(seeded, research)
    run_id, artifact_ids = task.runs[0].id, [a.id for a in task.artifacts]
    assert artifact_ids

    assert client.delete(f"/api/tasks/{task.id}").status_code == 204

    assert seeded.get(Task, task.id) is None
    assert seeded.get(ProviderRun, run_id) is None
    assert [seeded.get(Artifact, a) for a in artifact_ids] == [None] * len(artifact_ids)
    # The agent that did it is untouched, and still listed.
    assert seeded.get(Provider, research.id) is not None
    assert any(p["slug"] == "research" for p in client.get("/api/providers").json())


def test_removing_a_task_leaves_every_other_task_alone(client, seeded, research):
    keep = finished_task(seeded, research, "Keep this one")
    drop = finished_task(seeded, research, "Remove this one")

    client.delete(f"/api/tasks/{drop.id}")

    remaining = [t["title"] for t in client.get("/api/tasks").json()]
    assert remaining == ["Keep this one"]
    assert seeded.get(Task, keep.id) is not None


def test_the_automation_that_asked_for_the_work_survives_losing_it(client, seeded, research):
    """Its history loses the link; the automation itself keeps running."""
    from datetime import datetime, time, timezone

    automation = automation_service.create(
        seeded,
        instruction="Compare three note-taking apps",
        schedule=ScheduleSpec(recurrence=Recurrence.DAILY, at=time(9, 0), timezone="UTC"),
        provider=research,
        now=datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc),
    )
    seeded.commit()
    for claimed, run in automation_service.claim_due(seeded, now=datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)):
        task = automation_service.start_run(seeded, claimed, run)
        seeded.commit()
        task_service.execute_run(seeded, task.runs[-1].id)
    automation_service.settle_finished(seeded)
    occurrence = automation_service.last_run(seeded, automation)
    task_id = occurrence.task_id
    assert task_id is not None

    assert client.delete(f"/api/tasks/{task_id}").status_code == 204

    assert seeded.get(Automation, automation.id) is not None
    assert seeded.get(Automation, automation.id).next_run_at is not None  # still scheduled
    seeded.expire_all()  # the database nullified the link; read it back rather than trusting the session
    kept = seeded.get(AutomationRun, occurrence.id)
    assert kept is not None and kept.task_id is None  # the history stays, without a broken link
    assert client.get(f"/api/automations/{automation.id}").status_code == 200


def test_a_notification_about_a_removed_result_goes_with_it(client, seeded, research, monkeypatch):
    """A notification exists to point at a result. Without it there is nothing to show."""
    answers = iter(["One.", "Two."])
    monkeypatch.setattr(task_service, "execute", lambda *a, **k: (InvocationResult(state=ResultState.COMPLETED, summary=next(answers)), []))
    task = finished_task(seeded, research, "Watched thing")
    other = finished_task(seeded, research, "Unrelated thing")
    event = notification_service.raise_event(seeded, kind="automation.matched", title="Watched thing", task=task)
    unrelated = notification_service.raise_event(seeded, kind="automation.matched", title="Something else", task=other)
    seeded.commit()

    client.delete(f"/api/tasks/{task.id}")

    assert seeded.get(NotificationEvent, event.id) is None
    assert seeded.get(NotificationEvent, unrelated.id) is not None  # nothing else was touched
    listed = client.get("/api/notifications").json()
    assert [i["title"] for i in listed["items"]] == ["Something else"]
    assert all(i["task_id"] is not None for i in listed["items"])  # no broken links left


def test_work_still_running_is_not_pulled_out_from_under_it(client, seeded, research):
    task = task_service.submit(seeded, "Something long", provider=research)
    seeded.commit()
    response = client.delete(f"/api/tasks/{task.id}")
    assert response.status_code == 409
    assert "still working" in response.json()["detail"]
    assert seeded.get(Task, task.id) is not None


def test_removing_a_task_removes_the_file_it_produced(client, seeded, research):
    from app.services.artifacts import resolve_path

    task = finished_task(seeded, research)
    paths = [resolve_path(a) for a in task.artifacts]
    stored = [p for p in paths if p is not None]
    assert stored and all(p.is_file() for p in stored)

    client.delete(f"/api/tasks/{task.id}")
    assert not any(p.exists() for p in stored)


def test_a_task_that_is_not_there_says_so(client, seeded):
    assert client.delete(f"/api/tasks/{uuid.uuid4()}").status_code == 404


# --------------------------------------------------------------------------- clearing the lot

def test_clearing_history_empties_recent_and_nothing_else(client, seeded, research):
    from datetime import datetime, time, timezone

    for n in range(3):
        finished_task(seeded, research, f"Task {n}")
    automation = automation_service.create(
        seeded,
        instruction="Compare three note-taking apps",
        schedule=ScheduleSpec(recurrence=Recurrence.DAILY, at=time(9, 0), timezone="UTC"),
        provider=research,
        now=datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc),
    )
    seeded.commit()
    providers_before = {p["slug"] for p in client.get("/api/providers").json()}

    cleared = client.delete("/api/tasks")
    assert cleared.status_code == 200 and cleared.json() == {"removed": 3}

    assert client.get("/api/tasks").json() == []
    assert seeded.scalar(__import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).select_from(ProviderRun)) == 0
    # Everything that is not history is still here.
    assert {p["slug"] for p in client.get("/api/providers").json()} == providers_before
    assert [a["id"] for a in client.get("/api/automations").json()] == [str(automation.id)]
    assert seeded.get(Automation, automation.id).next_run_at is not None


def test_clearing_history_leaves_work_in_flight_alone(client, seeded, research):
    done = finished_task(seeded, research, "Finished")
    running = task_service.submit(seeded, "Still going", provider=research)
    seeded.commit()

    assert client.delete("/api/tasks").json() == {"removed": 1}

    assert seeded.get(Task, done.id) is None
    assert seeded.get(Task, running.id) is not None


def test_clearing_an_empty_history_is_harmless(client, seeded):
    assert client.delete("/api/tasks").json() == {"removed": 0}


# --------------------------------------------------------------------------- upgrading an existing workspace

def test_the_shipped_demos_are_retired_when_demo_mode_is_off(db):
    provider_service.seed_examples(db, demo=True)
    assert {p.slug for p in provider_service.list_providers(db, enabled_only=False)} == {"research", "calendar-demo", "claude-code"}

    provider_service.seed_examples(db, demo=False)
    assert {p.slug for p in provider_service.list_providers(db, enabled_only=False)} == {"claude-code"}


def test_a_demo_that_did_real_work_is_disabled_rather_than_deleted(db):
    """Its runs point at it, so removing it would take that history with it."""
    provider_service.seed_examples(db, demo=True)
    research = provider_service.get_by_slug(db, "research")
    task = task_service.submit(db, "Compare three note-taking apps", provider=research)
    db.commit()
    task_service.execute_run(db, task.runs[-1].id)
    db.commit()

    provider_service.seed_examples(db, demo=False)

    still_there = provider_service.get_by_slug(db, "research")
    assert still_there is not None and still_there.enabled is False
    assert db.get(Task, task.id) is not None  # the work is still readable
    assert provider_service.get_by_slug(db, "calendar-demo") is None  # that one did nothing, so it went


def test_a_demo_an_earlier_release_shipped_is_cleaned_up_even_in_demo_mode(db):
    """0.1.0 shipped an event demo; its handler is gone, so it could only fail."""
    old = provider_service.register_provider(
        db,
        {
            "slug": "event-demo",
            "name": "Event Allocation Demo",
            "description": "Allocate participants for an event (demo)",
            "capabilities": [{"id": "events.allocate", "title": "Allocate participants"}],
            "adapter": {"kind": "local", "ref": "providers.event_demo:run", "config": {}},
            "origin": "example",
        },
    )
    db.commit()
    assert old is not None

    provider_service.seed_examples(db, demo=True)

    slugs = {p.slug for p in provider_service.list_providers(db, enabled_only=False)}
    assert "event-demo" not in slugs and "calendar-demo" in slugs


def test_an_agent_of_your_own_called_research_is_never_touched(db):
    """The name is not the test: where the record came from is."""
    provider_service.seed_examples(db, demo=True)
    mine = provider_service.register_provider(
        db,
        {
            "name": "Research",
            "description": "My own research agent",
            "capabilities": [{"id": "research", "title": "Research"}],
            "adapter": {"kind": "http", "config": {"base_url": "http://localhost:9999"}},
            "origin": "connected",
        },
    )
    db.commit()

    provider_service.seed_examples(db, demo=False)

    assert db.get(Provider, mine.id) is not None
    assert provider_service.get_by_slug(db, mine.slug).enabled is True
    assert provider_service.shipped_demo(mine) is False


def test_a_shipped_demo_someone_repointed_is_left_alone(db):
    """If the adapter is no longer the shipped one, it is not Bevro's to remove."""
    provider_service.seed_examples(db, demo=True)
    research = provider_service.get_by_slug(db, "research")
    research.adapter = {"kind": "http", "ref": None, "config": {"base_url": "http://localhost:9999"}}
    db.commit()

    assert provider_service.shipped_demo(research) is False
    provider_service.seed_examples(db, demo=False)
    assert provider_service.get_by_slug(db, "research") is not None


# --------------------------------------------------------------------------- removing an agent

def test_an_agent_that_never_did_anything_is_simply_gone(client, seeded):
    provider = provider_service.register_provider(
        seeded,
        {
            "name": "Sales Desk",
            "description": "Quotes",
            "capabilities": [{"id": "research", "title": "Research"}],
            "adapter": {"kind": "http", "config": {"base_url": "http://localhost:9999"}},
            "origin": "connected",
        },
    )
    seeded.commit()

    assert client.delete(f"/api/providers/{provider.id}").status_code == 204
    assert seeded.get(Provider, provider.id) is None
    assert all(p["slug"] != provider.slug for p in client.get("/api/providers").json())


def test_an_agent_with_history_is_removed_and_its_work_stays(client, seeded, research):
    task = finished_task(seeded, research, "Compare three note-taking apps")
    run_id, artifact_ids = task.runs[0].id, [a.id for a in task.artifacts]
    assert artifact_ids
    research.origin = "connected"  # a shipped example cannot be removed; this stands for yours
    seeded.commit()

    assert client.delete(f"/api/providers/{research.id}").status_code == 204

    # The agent is gone...
    assert seeded.get(Provider, research.id) is None
    # ...and every trace of the work is still here.
    seeded.expire_all()
    kept = seeded.get(Task, task.id)
    assert kept is not None and kept.state == "completed"
    run = seeded.get(ProviderRun, run_id)
    assert run is not None and run.provider_id is None
    assert [seeded.get(Artifact, a) is not None for a in artifact_ids] == [True] * len(artifact_ids)


def test_history_still_says_who_did_the_work(client, seeded, research):
    task = finished_task(seeded, research, "Compare three note-taking apps")
    research.origin = "connected"
    seeded.commit()
    client.delete(f"/api/providers/{research.id}")

    listed = client.get("/api/tasks").json()
    assert [t["provider"]["name"] for t in listed] == ["Research"]
    assert listed[0]["provider"]["removed"] is True
    assert listed[0]["provider"]["id"] is None

    detail = client.get(f"/api/tasks/{task.id}").json()
    assert detail["runs"][0]["provider"] == {"id": None, "slug": "research", "name": "Research", "removed": True}
    assert detail["artifacts"], "the results are still readable"


def test_a_removed_agent_is_no_longer_offered_work(client, seeded, research):
    finished_task(seeded, research, "Compare three note-taking apps")
    research.origin = "connected"
    seeded.commit()
    client.delete(f"/api/providers/{research.id}")

    from app.routing.catalogue import build_catalogue

    seeded.expire_all()
    assert all(entry.id != "research" for entry in build_catalogue(seeded, selectable_only=False))
    response = client.post("/api/tasks", json={"request": "Compare three note-taking apps"})
    assert response.status_code == 503 and response.json()["detail"]["reason"] == "no_provider"


def test_an_automation_pinned_to_a_removed_agent_fails_honestly(client, seeded, research):
    from datetime import datetime, time, timezone

    automation = automation_service.create(
        seeded,
        instruction="Compare three note-taking apps",
        schedule=ScheduleSpec(recurrence=Recurrence.DAILY, at=time(9, 0), timezone="UTC"),
        provider=research,
        now=datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc),
    )
    seeded.commit()
    research.origin = "connected"
    seeded.commit()
    client.delete(f"/api/providers/{research.id}")
    seeded.expire_all()

    # The automation is still there, and says plainly why nothing ran.
    assert seeded.get(Automation, automation.id) is not None
    for claimed, run in automation_service.claim_due(seeded, now=datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)):
        assert automation_service.start_run(seeded, claimed, run) is None
        seeded.commit()
    last = automation_service.last_run(seeded, automation)
    assert last.outcome == "failed" and "no longer here" in last.reason


def test_the_same_name_can_be_used_again_afterwards(client, seeded, research):
    finished_task(seeded, research, "Compare three note-taking apps")
    research.origin = "connected"
    seeded.commit()
    client.delete(f"/api/providers/{research.id}")

    again = provider_service.register_provider(
        seeded,
        {
            "name": "Research",
            "description": "My own research agent this time",
            "capabilities": [{"id": "research", "title": "Research"}],
            "adapter": {"kind": "http", "config": {"base_url": "http://localhost:9999"}},
            "origin": "connected",
        },
    )
    seeded.commit()
    assert again.id is not None and again.name == "Research"
    # The old work still names the old agent, and is not attributed to the new one.
    detail = client.get("/api/tasks").json()[0]
    assert detail["provider"]["removed"] is True and detail["provider"]["id"] is None


def test_an_agent_in_the_middle_of_something_is_not_removed(client, seeded, research):
    task_service.submit(seeded, "Something long", provider=research)
    research.origin = "connected"
    seeded.commit()

    response = client.delete(f"/api/providers/{research.id}")
    assert response.status_code == 409
    assert "working on something right now" in response.json()["detail"]
    assert seeded.get(Provider, research.id) is not None


def test_removing_a_created_agent_takes_the_project_bevro_wrote(client, seeded, research, managed_data):
    """Generated code belongs to the agent. History does not.

    Both kinds of generated data - the project Bevro wrote and the connection
    it built - live under the test's own directories, never the
    installation's, and both must be gone afterwards.
    """
    from app.services import agents as agent_service
    from app.services import bridges as bridge_service

    built = agent_service.agent_dir(research.id)
    bridge = bridge_service.integration_dir(research.id)
    # Both really are inside this test's tmp_path, not a real data directory.
    assert managed_data.agents in built.parents, built
    assert managed_data.integrations in bridge.parents, bridge
    assert not str(built).startswith("/data") and not str(bridge).startswith("/data")

    built.mkdir(parents=True, exist_ok=True)
    (built / "agent.py").write_text("print('hi')\n", encoding="utf-8")
    bridge.mkdir(parents=True, exist_ok=True)
    (bridge / "bridge.py").write_text("print('hi')\n", encoding="utf-8")
    finished_task(seeded, research, "Compare three note-taking apps")
    research.origin = "created"
    seeded.commit()

    plan = client.get(f"/api/providers/{research.id}/removal").json()
    assert plan["built_project"] is True and plan["built_connection"] is True and plan["history"] == 1

    assert client.delete(f"/api/providers/{research.id}").status_code == 204
    assert not built.exists() and not bridge.exists()
    assert len(client.get("/api/tasks").json()) == 1  # the work it did is still there


def test_no_test_ever_writes_to_the_installation_s_own_directories():
    """The floor under every test: nothing points at /data.

    The defaults are the real installation's. A test that reached them would
    pollute a developer's workspace, and would simply fail on a bare runner
    where /data does not exist.
    """
    from app.config import get_settings

    settings = get_settings()
    for name in ("artifact_dir", "log_dir", "agents_dir", "integrations_dir", "workspaces_file"):
        value = getattr(settings, name)
        assert not value.startswith("/data"), f"{name} points at the real installation: {value}"


def test_the_removal_question_is_asked_with_the_facts(client, seeded, research):
    finished_task(seeded, research, "One")
    finished_task(seeded, research, "Two")
    plan = client.get(f"/api/providers/{research.id}/removal").json()
    assert plan == {"removable": True, "history": 2, "in_flight": 0, "credentials": 0, "built_project": False, "built_connection": False}
    # Shipped or not, it is the person's to remove; its two tasks stay.
    assert client.delete(f"/api/providers/{research.id}").status_code == 204
    assert len(client.get("/api/tasks").json()) == 2
