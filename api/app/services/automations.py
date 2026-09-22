"""Running work again: claiming what is due, and deciding what to say about it.

    Automation → (due) → an ordinary Task → the usual ProviderRun and runtime
    → artifacts → for monitoring, a verdict → quiet, or surfaced.

There is no second execution path: a scheduled run is created and driven by
the same task service a person's request uses, so permissions, credentials,
workspaces, availability and runtime fallback all behave identically.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.automations.conditions import ConditionKind, ConditionSpec, Verdict, evaluate, parse_condition
from app.automations.schedule import ScheduleSpec, has_recurring_intent, next_occurrence, parse_schedule, strip_schedule_words, wants_monitoring
from app.models import Automation, AutomationRun, Provider, Task
from app.models._common import utcnow
from app.services import notifications as notification_service
from app.services import providers as provider_service
from app.services import tasks as task_service

log = logging.getLogger("bevro.automations")

# A run that is due but more than this late means Bevro was away: it happens
# once now rather than once for every turn that was missed.
LATE_AFTER_SECONDS = 300
TITLE_MAX = 80
SUMMARY_MAX = 4000


class AutomationError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- reading an instruction

def read_intent(text: str, *, tz: str = "UTC", now: datetime | None = None) -> dict[str, Any] | None:
    """Does this sentence ask for work to happen again? What, and when?

    Returns what Bevro would set up, for the person to confirm. Nothing
    recurring is ever created without being shown first.
    """
    spec = parse_schedule(text, tz=tz, now=now)
    if spec is None:
        return None
    monitoring = wants_monitoring(text)
    condition = parse_condition(text) if monitoring else ConditionSpec(kind=ConditionKind.ALWAYS)
    instruction = strip_schedule_words(text)
    return {
        "instruction": instruction,
        "title": title_for(instruction),
        "schedule": spec,
        "mode": "monitoring" if monitoring else "scheduled",
        "condition": condition,
        "describe": spec.describe(),
        "condition_describe": condition.describe() if monitoring else None,
    }


def title_for(instruction: str) -> str:
    words = " ".join((instruction or "").split())
    if len(words) <= TITLE_MAX:
        return words or "Automation"
    return words[:TITLE_MAX].rsplit(" ", 1)[0] + "…"


# --------------------------------------------------------------------------- creating and editing

def create(
    db: Session,
    *,
    instruction: str,
    schedule: ScheduleSpec,
    mode: str = "scheduled",
    condition: ConditionSpec | None = None,
    provider: Provider | None = None,
    title: str | None = None,
    notify: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Automation:
    """Set up work that should happen again. Caller commits."""
    moment = now or utcnow()
    due = next_occurrence(schedule, moment)
    if due is None:
        raise AutomationError("That schedule has no future date.", 422)
    automation = Automation(
        title=(title or title_for(instruction))[:TITLE_MAX],
        instruction=instruction.strip(),
        mode=mode,
        selection="pinned" if provider is not None else "dynamic",
        provider_id=provider.id if provider is not None else None,
        schedule=schedule.model_dump(mode="json"),
        condition=(condition or ConditionSpec()).model_dump(mode="json") if mode == "monitoring" else None,
        enabled=True,
        next_run_at=due,
        notify=notification_service.preferences(notify),
    )
    db.add(automation)
    db.flush()
    log.info("automation %s set up: %s (%s)", automation.id, schedule.describe(), automation.selection)
    return automation


def from_task(db: Session, task: Task, *, schedule: ScheduleSpec, mode: str = "scheduled", condition: ConditionSpec | None = None, notify: dict[str, Any] | None = None, now: datetime | None = None) -> Automation:
    """Schedule something that already worked once: the same words, the same provider."""
    provider = task.runs[-1].provider if task.runs else None
    return create(
        db,
        instruction=task.original_request,
        schedule=schedule,
        mode=mode,
        condition=condition,
        provider=provider,  # what worked before is pinned; the person can change it
        title=title_for(task.title),
        notify=notify,
        now=now,
    )


def update(db: Session, automation: Automation, *, instruction: str | None = None, schedule: ScheduleSpec | None = None, condition: ConditionSpec | None = None, mode: str | None = None, enabled: bool | None = None, notify: dict[str, Any] | None = None, now: datetime | None = None) -> Automation:
    """Change what is asked, when, or whether it runs. History is untouched."""
    moment = now or utcnow()
    if instruction is not None and instruction.strip():
        automation.instruction = instruction.strip()
        automation.title = title_for(instruction)
    if mode is not None:
        automation.mode = mode
    if notify is not None:
        automation.notify = notification_service.preferences({**notification_service.preferences(automation.notify), **notify})
    if condition is not None:
        automation.condition = condition.model_dump(mode="json")
    if schedule is not None:
        automation.schedule = schedule.model_dump(mode="json")
        automation.next_run_at = next_occurrence(schedule, moment)
    if enabled is not None and enabled != automation.enabled:
        automation.enabled = enabled
        # Resuming works out the next turn from now; pausing simply stops.
        automation.next_run_at = next_occurrence(spec_of(automation), moment) if enabled else None
    db.flush()
    return automation


def spec_of(automation: Automation) -> ScheduleSpec:
    return ScheduleSpec.model_validate(automation.schedule or {})


def condition_of(automation: Automation) -> ConditionSpec:
    return ConditionSpec.model_validate(automation.condition or {})


def list_automations(db: Session) -> list[Automation]:
    return list(db.scalars(select(Automation).order_by(Automation.created_at.desc())))


def get(db: Session, automation_id: uuid.UUID) -> Automation | None:
    return db.get(Automation, automation_id)


# --------------------------------------------------------------------------- claiming what is due

def claim_due(db: Session, *, now: datetime | None = None, limit: int = 10) -> list[tuple[Automation, AutomationRun]]:
    """Take the automations that are due, one scheduler only.

    The row is locked and its next turn is written before anything else
    happens, and an occurrence is unique per automation, so two schedulers
    cannot both create the same run.
    """
    moment = now or utcnow()
    rows = db.execute(
        sql_text(
            """
            SELECT id FROM automations
            WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= :now
            ORDER BY next_run_at
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"now": moment, "limit": limit},
    ).all()
    claimed: list[tuple[Automation, AutomationRun]] = []
    for (automation_id,) in rows:
        automation = db.get(Automation, automation_id)
        if automation is None or not automation.enabled or automation.next_run_at is None:
            continue
        due = automation.next_run_at
        late = (moment - due).total_seconds() > LATE_AFTER_SECONDS
        run = AutomationRun(automation_id=automation.id, scheduled_for=due, delayed=late, trigger="catch_up" if late else "schedule")
        db.add(run)
        try:
            db.flush()
        except IntegrityError:
            # Another scheduler already took this occurrence.
            db.rollback()
            continue
        # Whatever was missed while Bevro was away is skipped: the work happens
        # once now, and the next turn is the next real one.
        automation.next_run_at = next_occurrence(spec_of(automation), moment)
        automation.last_run_at = moment
        db.flush()
        claimed.append((automation, run))
    db.commit()
    return claimed


# --------------------------------------------------------------------------- running one

def start_run(db: Session, automation: Automation, run: AutomationRun) -> Task | None:
    """Create the ordinary Task this occurrence is. Caller commits."""
    provider = None
    if automation.selection == "pinned":
        provider = db.get(Provider, automation.provider_id) if automation.provider_id else None
        if provider is None:
            _finish(db, run, "failed", reason="The agent this was set up with is no longer here.")
            return None
        if not provider_service.is_available(provider):
            # A pinned agent is never silently replaced: the turn is recorded
            # as failed and the next one comes round as usual.
            _finish(db, run, "failed", reason=f"{provider.name} wasn't available at {run.scheduled_for:%H:%M}.")
            return None
    try:
        task = task_service.submit(db, automation.instruction, provider=provider)
    except task_service.NoProviderAvailable as exc:
        _finish(db, run, "failed", reason=str(exc))
        return None
    except (task_service.InvalidInput, ValueError) as exc:
        _finish(db, run, "failed", reason=str(exc))
        return None
    task.routing = {**(task.routing or {}), "automation": {"id": str(automation.id), "run_id": str(run.id), "scheduled_for": run.scheduled_for.isoformat(), "delayed": run.delayed, "trigger": run.trigger}}
    run.task_id = task.id
    run.started_at = utcnow()
    run.outcome = "running"
    db.flush()
    log.info("automation %s: task %s created for %s", automation.id, task.id, run.scheduled_for.isoformat())
    return task


def run_now(db: Session, automation: Automation, *, now: datetime | None = None) -> Task | None:
    """Do it at once. The recurrence is untouched."""
    moment = now or utcnow()
    run = AutomationRun(automation_id=automation.id, scheduled_for=moment, trigger="run_now")
    db.add(run)
    db.flush()
    task = start_run(db, automation, run)
    db.commit()
    return task


# --------------------------------------------------------------------------- what came back

def settle(db: Session, automation: Automation, run: AutomationRun, task: Task, *, model: Any = None) -> Verdict | None:
    """Record how the occurrence went, and for monitoring decide whether to say so."""
    summary = (task.summary or "").strip()
    text = _result_text(task)
    if task.state == "failed":
        reason = summary or "The work didn't finish."
        _finish(db, run, "failed", reason=reason)
        db.commit()
        if notify_preferences(automation).get("on_finish"):
            notify(db, automation, run, task, kind="automation.failed", reason=reason)
        return None

    if automation.mode != "monitoring":
        # Every run is the point. Results go to Recent; the person is told
        # only if they asked to be.
        _finish(db, run, "completed", matched=True, reason=None, surfaced=True)
        automation.last_summary = text[:SUMMARY_MAX]
        db.commit()
        if notify_preferences(automation).get("on_finish"):
            notify(db, automation, run, task, kind="automation.finished", reason="It finished.")
        return None

    condition = condition_of(automation)
    verdict = evaluate(condition, current=text, previous=automation.last_summary, previous_digest=automation.last_digest, model=model)
    _finish(db, run, "completed", matched=verdict.matched, reason=verdict.reason, surfaced=verdict.matched)
    automation.last_digest = verdict.digest
    automation.last_summary = text[:SUMMARY_MAX]
    db.commit()
    if verdict.matched:
        notify(db, automation, run, task, kind="automation.matched", reason=verdict.reason)
    else:
        log.info("automation %s: quiet (%s)", automation.id, verdict.reason)
    return verdict


def _result_text(task: Task) -> str:
    """What the run produced, as text: the summary and any readable artifact."""
    parts = [task.summary or ""]
    for artifact in task.artifacts:
        payload = artifact.payload if isinstance(artifact.payload, dict) else None
        if payload and isinstance(payload.get("text"), str):
            parts.append(payload["text"])
        elif isinstance(artifact.payload, (dict, list)):
            parts.append(str(artifact.payload))
    return "\n\n".join(p for p in parts if p).strip()


def _finish(db: Session, run: AutomationRun, outcome: str, *, matched: bool | None = None, reason: str | None = None, surfaced: bool = False) -> None:
    run.outcome = outcome
    run.matched = matched
    run.reason = reason
    run.surfaced = surfaced
    run.started_at = run.started_at or utcnow()
    db.flush()


# --------------------------------------------------------------------------- telling the person

def notify_preferences(automation: Automation) -> dict[str, bool]:
    """How this automation should be announced. Bevro's defaults fill the gaps."""
    return notification_service.preferences(automation.notify)


def notify(db: Session, automation: Automation, run: AutomationRun, task: Task | None, *, kind: str, reason: str | None) -> None:
    """The one place an occurrence is announced.

    Everything about *how* the person hears it lives in the delivery layer.
    This decides only that there is something worth saying, and hands it over.
    """
    log.info("automation %s: worth telling about - %s", automation.id, reason)
    notification_service.raise_event(
        db,
        kind=kind,
        title=automation.title,
        summary=(task.summary if task is not None else None) or automation.last_summary,
        reason=reason,
        automation=automation,
        run=run,
        task=task,
        prefs=automation.notify,
    )
    db.commit()


def history(db: Session, automation: Automation, limit: int = 20) -> list[AutomationRun]:
    return list(db.scalars(select(AutomationRun).where(AutomationRun.automation_id == automation.id).order_by(AutomationRun.scheduled_for.desc()).limit(limit)))


def waiting_runs(db: Session, limit: int = 20) -> list[AutomationRun]:
    """Occurrences whose task is still sitting there, waiting for someone quick.

    Long-running work belongs to the worker and is left alone.
    """
    from app.domain.run_state import RunState
    from app.models import ProviderRun

    rows = db.scalars(
        select(AutomationRun)
        .join(Task, Task.id == AutomationRun.task_id)
        .join(ProviderRun, ProviderRun.task_id == Task.id)
        .where(
            AutomationRun.outcome == "running",
            ProviderRun.state == RunState.PENDING,
            ProviderRun.execution == "inline",
            ProviderRun.input_request.is_(None),
        )
        .limit(limit)
    ).all()
    return list(rows)


def last_run(db: Session, automation: Automation) -> AutomationRun | None:
    return db.scalars(select(AutomationRun).where(AutomationRun.automation_id == automation.id).order_by(AutomationRun.scheduled_for.desc()).limit(1)).first()


# --------------------------------------------------------------------------- settling finished work

def settle_finished(db: Session, *, model: Any = None) -> int:
    """Look at occurrences whose task has finished, and close them.

    Called by the scheduler each turn: the work itself is driven by the
    ordinary task machinery, so this only reads the outcome.
    """
    running = db.scalars(select(AutomationRun).where(AutomationRun.outcome == "running", AutomationRun.task_id.isnot(None)).limit(50)).all()
    settled = 0
    for run in running:
        task = task_service.get_task(db, run.task_id)
        if task is None:
            _finish(db, run, "failed", reason="The work record is gone.")
            db.commit()
            settled += 1
            continue
        if task.state not in ("completed", "failed", "cancelled"):
            continue
        automation = db.get(Automation, run.automation_id)
        if automation is None:
            continue
        settle(db, automation, run, task, model=model)
        settled += 1
    return settled
