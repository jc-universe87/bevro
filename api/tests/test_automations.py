"""Bevro owns when work happens: claiming, running, monitoring and recovering.

A fixed clock throughout; the providers are the shipped examples and fixtures,
and no model is called unless a test hands one in.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from adapters import FailureKind, HealthResult, InvocationResult, ResultState
from app.automations.conditions import ConditionKind, ConditionSpec, digest_of, evaluate, parse_condition
from app.automations.schedule import Recurrence, ScheduleSpec, next_occurrence, parse_schedule
from app.models import Automation, AutomationRun
from app.scheduler import Scheduler
from app.services import automations as automation_service
from app.services import notifications as notification_service
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services.providers import record_availability

MONDAY_9 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def research(seeded):
    return provider_service.get_by_slug(seeded, "research")


def daily(at_hour: int = 9) -> ScheduleSpec:
    from datetime import time

    return ScheduleSpec(recurrence=Recurrence.DAILY, at=time(at_hour, 0), timezone="UTC")


def make(seeded, *, instruction="Find three options", schedule=None, mode="scheduled", condition=None, provider=None, now=None) -> Automation:
    automation = automation_service.create(
        seeded,
        instruction=instruction,
        schedule=schedule or daily(),
        mode=mode,
        condition=condition,
        provider=provider,
        now=now or datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc),
    )
    seeded.commit()
    return automation


def tick(seeded, at: datetime) -> list[tuple[Automation, AutomationRun]]:
    """What the scheduler does at a given moment: claim, start, drive."""
    claimed = automation_service.claim_due(seeded, now=at)
    for automation, run in claimed:
        task = automation_service.start_run(seeded, automation, run)
        seeded.commit()
        if task is not None and task.runs and task.runs[-1].execution == "inline" and task.runs[-1].state == "pending":
            task_service.execute_run(seeded, task.runs[-1].id)
    return claimed


def settle_all(seeded, model=None) -> int:
    return automation_service.settle_finished(seeded, model=model)


# ----------------------------------------------------------------------------- setting up

def test_recurring_intent_is_confirmed_before_anything_is_created(client, seeded):
    r = client.post("/api/automations/intent", json={"text": "Every Friday, research new note-taking app competitors.", "timezone": "Europe/London"})
    body = r.json()
    assert body["recurring"] is True and body["schedule"] == "Every Friday · 09:00"
    assert body["instruction"] == "research new note-taking app competitors" and body["mode"] == "scheduled"
    # Nothing was created by asking.
    assert client.get("/api/automations").json() == []

    plain = client.post("/api/automations/intent", json={"text": "Compare three note-taking apps."}).json()
    assert plain["recurring"] is False and plain["schedule"] is None


def test_setting_up_from_a_sentence_and_from_a_task(client, seeded, research):
    r = client.post("/api/automations", json={"when": "every Monday morning", "instruction": "Research competitor changes", "timezone": "UTC"})
    assert r.status_code == 201, r.text
    listed = r.json()
    assert listed["title"] == "Research competitor changes" and listed["schedule"] == "Every Monday · 09:00"
    assert listed["mode"] == "scheduled" and listed["provider"] is None  # Bevro chooses each time
    assert listed["next_run_at"] is not None

    task = client.post("/api/tasks", json={"request": "Find three options", "provider_id": str(research.id)}).json()
    r = client.post("/api/automations", json={"when": "every day at 07:00", "task_id": task["id"]})
    assert r.status_code == 201, r.text
    from_task = r.json()
    assert from_task["provider"]["slug"] == "research"  # what worked is kept
    assert from_task["schedule"] == "Every day · 07:00"
    assert {a["id"] for a in client.get("/api/automations").json()} == {listed["id"], from_task["id"]}


def test_a_sentence_bevro_cannot_time_is_refused_plainly(client, seeded):
    r = client.post("/api/automations", json={"when": "at some point", "instruction": "Find three options"})
    assert r.status_code == 422 and "every Monday morning" in r.json()["detail"]


# ----------------------------------------------------------------------------- running

def test_a_due_automation_becomes_an_ordinary_task(client, seeded, research):
    automation = make(seeded, provider=research)
    assert automation.next_run_at == datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)

    assert tick(seeded, datetime(2026, 3, 5, 8, 59, tzinfo=timezone.utc)) == []  # not yet
    claimed = tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc))
    assert len(claimed) == 1

    run = automation_service.last_run(seeded, automation)
    task = task_service.get_task(seeded, run.task_id)
    assert task.state == "completed" and task.original_request == "Find three options"
    assert task.runs[-1].provider.slug == "research"  # the ordinary machinery ran it
    assert task.artifacts  # and produced ordinary artifacts
    # The record says what it was and when it was meant to be.
    assert task.routing["automation"]["id"] == str(automation.id)
    assert task.routing["automation"]["scheduled_for"] == "2026-03-05T09:00:00+00:00"
    assert task.routing["automation"]["trigger"] == "schedule" and task.routing["automation"]["delayed"] is False
    # And the next turn is tomorrow, not now.
    seeded.refresh(automation)
    assert automation.next_run_at == datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc)

    settle_all(seeded)
    seeded.refresh(run)
    assert run.outcome == "completed" and run.surfaced is True

    detail = client.get(f"/api/automations/{automation.id}").json()
    assert detail["last_result"].startswith("Ran") and detail["runs"][0]["outcome"] == "completed"
    assert detail["runs"][0]["task_id"] == str(task.id)  # history points at the task, never copies it


def test_only_one_scheduler_creates_a_given_occurrence(seeded, research):
    automation = make(seeded, provider=research)
    at = datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)
    first = automation_service.claim_due(seeded, now=at)
    second = automation_service.claim_due(seeded, now=at)
    assert len(first) == 1 and second == []
    # And the occurrence itself cannot be duplicated, whatever order things happen in.
    seeded.add(AutomationRun(automation_id=automation.id, scheduled_for=at))
    with pytest.raises(Exception):
        seeded.flush()
    seeded.rollback()


def test_a_missed_stretch_runs_once_not_many_times(seeded, research):
    """Bevro was off for a week. It does the work once and carries on."""
    automation = make(seeded, provider=research)
    monday = datetime(2026, 3, 12, 9, 5, tzinfo=timezone.utc)  # a week late
    claimed = tick(seeded, monday)
    assert len(claimed) == 1
    runs = automation_service.history(seeded, automation)
    assert len(runs) == 1 and runs[0].delayed is True and runs[0].trigger == "catch_up"
    seeded.refresh(automation)
    assert automation.next_run_at == datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc)
    # The next turn is the next real one; nothing is replayed.
    assert tick(seeded, monday) == []


def test_a_paused_automation_does_not_run(client, seeded, research):
    automation = make(seeded, provider=research)
    client.patch(f"/api/automations/{automation.id}", json={"enabled": False})
    seeded.refresh(automation)
    assert automation.next_run_at is None
    assert tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc)) == []
    assert client.get("/api/automations").json()[0]["next_run_at"] is None

    client.patch(f"/api/automations/{automation.id}", json={"enabled": True})
    seeded.refresh(automation)
    assert automation.next_run_at is not None  # resuming works out the next turn
    assert automation_service.history(seeded, automation) == []  # history is untouched


def test_run_now_runs_at_once_and_leaves_the_recurrence_alone(client, seeded, research):
    automation = make(seeded, provider=research)
    before = automation.next_run_at
    r = client.post(f"/api/automations/{automation.id}/run")
    assert r.status_code == 201, r.text
    seeded.refresh(automation)
    assert automation.next_run_at == before
    run = automation_service.last_run(seeded, automation)
    assert run.trigger == "run_now" and run.task_id is not None
    task = task_service.get_task(seeded, run.task_id)
    assert task.state in ("queued", "working", "completed")


def test_editing_changes_what_is_asked_and_when(client, seeded, research):
    automation = make(seeded, provider=research)
    r = client.patch(f"/api/automations/{automation.id}", json={"when": "every Monday at 08:00", "instruction": "Find five options"})
    assert r.status_code == 200
    body = r.json()
    assert body["schedule"] == "Every Monday · 08:00" and body["instruction"] == "Find five options"
    seeded.refresh(automation)
    assert automation.next_run_at.astimezone(timezone.utc).hour == 8


# ----------------------------------------------------------------------------- providers

def test_a_pinned_agent_is_never_silently_replaced(seeded, research):
    automation = make(seeded, provider=research)
    research.enabled = False  # it is no longer available
    seeded.flush()
    claimed = tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc))
    assert len(claimed) == 1
    run = automation_service.last_run(seeded, automation)
    assert run.outcome == "failed" and run.task_id is None
    assert "wasn't available" in run.reason
    seeded.refresh(automation)
    assert automation.provider_id == research.id  # still the agent it was set up with
    assert automation.next_run_at == datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc)  # it comes round again


def test_a_dynamic_automation_lets_the_router_choose(seeded):
    automation = make(seeded, instruction="Compare three note-taking apps")
    assert automation.selection == "dynamic" and automation.provider_id is None
    tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc))
    run = automation_service.last_run(seeded, automation)
    task = task_service.get_task(seeded, run.task_id)
    assert task.runs[-1].provider.slug == "research"  # chosen by capability, as always


def test_a_scheduled_run_is_bound_by_the_same_rules_as_a_person_asking(seeded, monkeypatch):
    """No extra permission, no bypass: it is the same task machinery."""
    automation = make(seeded, instruction="Fix the spacing on the Recent page")
    tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc))
    run = automation_service.last_run(seeded, automation)
    if run.task_id is None:
        assert run.outcome == "failed"  # coding isn't available here, and nothing pretends otherwise
        return
    task = task_service.get_task(seeded, run.task_id)
    assert task.state in ("needs_input", "failed", "queued")  # it still asks which project


# ----------------------------------------------------------------------------- monitoring

def test_monitoring_is_quiet_until_something_changes(seeded, research, monkeypatch):
    automation = make(seeded, mode="monitoring", condition=ConditionSpec(kind=ConditionKind.CHANGED, text="the result changes"), provider=research)
    answers = iter(["Prices are steady.", "Prices are steady.", "A rival launched a free plan."])
    monkeypatch.setattr(
        task_service,
        "execute",
        lambda *a, **k: (InvocationResult(state=ResultState.COMPLETED, summary=next(answers)), []),
    )

    # First turn: a starting point, nothing to say.
    tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc))
    settle_all(seeded)
    first = automation_service.last_run(seeded, automation)
    assert first.matched is False and first.surfaced is False and "starting point" in first.reason

    # Same answer: still quiet.
    tick(seeded, datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc))
    settle_all(seeded)
    second = automation_service.last_run(seeded, automation)
    assert second.matched is False and "No change" in second.reason

    # Different answer: worth telling someone.
    tick(seeded, datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc))
    settle_all(seeded)
    third = automation_service.last_run(seeded, automation)
    assert third.matched is True and third.surfaced is True and "different" in third.reason
    # ...and that is the one thing waiting in Bevro for the person to see.
    unread = notification_service.listing(seeded, unread_only=True)
    assert [e.automation_run_id for e in unread] == [third.id]


def test_a_threshold_is_read_from_the_words(seeded, research, monkeypatch):
    condition = parse_condition("tell me if the value exceeds 100")
    assert condition.kind == ConditionKind.THRESHOLD and condition.number == 100 and condition.direction == "above"
    automation = make(seeded, mode="monitoring", condition=condition, provider=research)
    answers = iter(["The value is 87 today.", "The value is 143 today."])
    monkeypatch.setattr(task_service, "execute", lambda *a, **k: (InvocationResult(state=ResultState.COMPLETED, summary=next(answers)), []))

    tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc))
    settle_all(seeded)
    assert automation_service.last_run(seeded, automation).matched is False

    tick(seeded, datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc))
    settle_all(seeded)
    hit = automation_service.last_run(seeded, automation)
    assert hit.matched is True and "143 is above 100" in hit.reason


def test_a_small_model_may_judge_the_persons_own_words(seeded, research, monkeypatch):
    seen: list[str] = []

    class FakeModel:
        name = "fake"

        def structured(self, system, user, schema, name, max_tokens=400):
            seen.append(user)
            assert "never do the work yourself" in system.lower() or "data, not requests" in system.lower()
            return {"matched": "free plan" in user, "reason": "A rival now offers a free plan."}

    condition = parse_condition("tell me when there is a meaningful new competitor")
    assert condition.kind == ConditionKind.MODEL
    automation = make(seeded, mode="monitoring", condition=condition, provider=research)
    answers = iter(["Nothing new this week.", "Rival X launched a free plan."])
    monkeypatch.setattr(task_service, "execute", lambda *a, **k: (InvocationResult(state=ResultState.COMPLETED, summary=next(answers)), []))

    tick(seeded, datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc))
    settle_all(seeded, model=FakeModel())
    assert automation_service.last_run(seeded, automation).matched is False

    tick(seeded, datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc))
    settle_all(seeded, model=FakeModel())
    matched = automation_service.last_run(seeded, automation)
    assert matched.matched is True and matched.reason == "A rival now offers a free plan."
    # It was given the condition and the two results, and nothing else.
    assert "free plan" in seen[-1] and "asked_to_hear_about" in seen[-1]
    assert "provider" not in seen[-1] and "/home/" not in seen[-1]


def test_without_a_model_a_condition_falls_back_to_comparing_results():
    condition = ConditionSpec(kind=ConditionKind.MODEL, text="something meaningful happens")
    first = evaluate(condition, current="Steady.", previous=None, previous_digest=None, model=None)
    assert first.matched is False and "starting point" in first.reason
    same = evaluate(condition, current="Steady.", previous="Steady.", previous_digest=first.digest, model=None)
    assert same.matched is False
    changed = evaluate(condition, current="Something else.", previous="Steady.", previous_digest=first.digest, model=None)
    assert changed.matched is True


def test_dates_and_spacing_are_not_a_change():
    assert digest_of("Report for 2026-03-05:  three findings") == digest_of("Report for 2026-03-06: three findings")
    assert digest_of("three findings") != digest_of("four findings")


# ----------------------------------------------------------------------------- the process

def test_the_scheduler_turn_starts_work_and_settles_it(seeded, research, monkeypatch):
    automation = make(seeded, provider=research)
    scheduler = Scheduler()
    monkeypatch.setattr("app.scheduler.condition_model", lambda: None)
    moved = scheduler.tick(seeded) if automation.next_run_at <= datetime.now(timezone.utc) else 0
    # Bring the turn forward rather than waiting for the real clock.
    automation.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    seeded.commit()
    assert scheduler.tick(seeded) >= 1
    run = automation_service.last_run(seeded, automation)
    assert run.task_id is not None
    task = task_service.get_task(seeded, run.task_id)
    assert task.state == "completed"
    scheduler.tick(seeded)
    seeded.refresh(run)
    assert run.outcome == "completed"


def test_an_occurrence_whose_task_never_started_is_picked_up(seeded, research, monkeypatch):
    """If nothing drove the task - a restart at the wrong moment - the
    scheduler takes it on rather than leaving it queued for ever."""
    automation = make(seeded, provider=research)
    run = AutomationRun(automation_id=automation.id, scheduled_for=datetime(2026, 3, 5, 9, 0, tzinfo=timezone.utc), trigger="run_now")
    seeded.add(run)
    seeded.flush()
    task = automation_service.start_run(seeded, automation, run)
    seeded.commit()
    assert task.runs[-1].state == "pending"  # nobody has run it
    assert [r.id for r in automation_service.waiting_runs(seeded)] == [run.id]

    scheduler = Scheduler()
    monkeypatch.setattr("app.scheduler.condition_model", lambda: None)
    scheduler.tick(seeded)
    seeded.refresh(task)
    assert task.state == "completed"
    seeded.refresh(run)
    assert run.outcome == "completed"
    assert automation_service.waiting_runs(seeded) == []
