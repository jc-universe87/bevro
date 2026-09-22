"""Repeating work: setting it up, seeing it, and changing it."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.automations.conditions import ConditionKind, ConditionSpec, parse_condition
from app.automations.schedule import parse_schedule
from app.db import get_db
from app.models import Automation
from app.schemas.automations import AutomationDetail, AutomationOut, AutomationUpdate, IntentIn, IntentOut, ScheduleIn
from app.schemas.serialise import automation_detail, automation_out
from app.services import automations as automation_service
from app.services import tasks as task_service

router = APIRouter(prefix="/automations", tags=["automations"])


def _load(db: Session, automation_id: uuid.UUID) -> Automation:
    automation = automation_service.get(db, automation_id)
    if automation is None:
        raise HTTPException(404, "That automation wasn't found.")
    return automation


@router.post("/intent", response_model=IntentOut)
def read_intent(body: IntentIn) -> IntentOut:
    """Does this sentence ask for repeating work? Home asks before doing anything."""
    intent = automation_service.read_intent(body.text, tz=body.timezone)
    if intent is None:
        return IntentOut(recurring=False)
    return IntentOut(
        recurring=True,
        title=intent["title"],
        instruction=intent["instruction"],
        schedule=intent["describe"],
        condition=intent["condition_describe"],
        mode=intent["mode"],
    )


@router.get("", response_model=list[AutomationOut])
def list_automations(db: Session = Depends(get_db)) -> list[AutomationOut]:
    return [automation_out(db, a) for a in automation_service.list_automations(db)]


@router.post("", response_model=AutomationOut, status_code=201)
def create_automation(body: ScheduleIn, db: Session = Depends(get_db)) -> AutomationOut:
    """Set work up to happen again - from a sentence, or from a task that worked."""
    schedule = parse_schedule(body.when, tz=body.timezone)
    if schedule is None:
        raise HTTPException(422, "Bevro couldn't tell when to run this. Try something like “every Monday morning”.")
    monitoring = bool(body.only_when and body.only_when.strip())
    prefs = body.notify.model_dump() if body.notify is not None else None
    condition: ConditionSpec | None = parse_condition(body.only_when or "") if monitoring else None
    mode = "monitoring" if monitoring else "scheduled"
    try:
        if body.task_id is not None:
            task = task_service.get_task(db, body.task_id)
            if task is None:
                raise HTTPException(404, "That task wasn't found.")
            automation = automation_service.from_task(db, task, schedule=schedule, mode=mode, condition=condition, notify=prefs)
        else:
            instruction = (body.instruction or "").strip()
            if len(instruction) < 3:
                raise HTTPException(422, "Bevro needs to know what to run.")
            intent = automation_service.read_intent(body.when, tz=body.timezone)
            automation = automation_service.create(
                db,
                instruction=instruction,
                schedule=schedule,
                mode=mode if monitoring else (intent or {}).get("mode", "scheduled"),
                condition=condition or ((intent or {}).get("condition") if (intent or {}).get("mode") == "monitoring" else None),
                notify=prefs,
            )
    except automation_service.AutomationError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    db.commit()
    db.refresh(automation)
    return automation_out(db, automation)


@router.get("/{automation_id}", response_model=AutomationDetail)
def get_automation(automation_id: uuid.UUID, db: Session = Depends(get_db)) -> AutomationDetail:
    return automation_detail(db, _load(db, automation_id))


@router.patch("/{automation_id}", response_model=AutomationOut)
def update_automation(automation_id: uuid.UUID, body: AutomationUpdate, db: Session = Depends(get_db)) -> AutomationOut:
    automation = _load(db, automation_id)
    schedule = None
    if body.when and body.when.strip():
        schedule = parse_schedule(body.when, tz=body.timezone)
        if schedule is None:
            raise HTTPException(422, "Bevro couldn't tell when to run this. Try something like “every Monday morning”.")
    condition = None
    mode = None
    if body.only_when is not None:
        if body.only_when.strip():
            condition, mode = parse_condition(body.only_when), "monitoring"
        else:
            condition, mode = ConditionSpec(kind=ConditionKind.ALWAYS), "scheduled"
    automation_service.update(
        db,
        automation,
        instruction=body.instruction,
        schedule=schedule,
        condition=condition,
        mode=mode,
        enabled=body.enabled,
        notify=body.notify.model_dump() if body.notify is not None else None,
    )
    db.commit()
    db.refresh(automation)
    return automation_out(db, automation)


@router.post("/{automation_id}/run", response_model=AutomationOut, status_code=201)
def run_now(automation_id: uuid.UUID, background: BackgroundTasks, db: Session = Depends(get_db)) -> AutomationOut:
    """Do it now. The recurrence is not affected."""
    automation = _load(db, automation_id)
    task = automation_service.run_now(db, automation)
    if task is not None:
        # Quick providers run here, exactly as when a person asks; long ones
        # are the worker's.
        run = task.runs[-1] if task.runs else None
        if run is not None and run.execution == "inline" and run.state == "pending":
            background.add_task(task_service.execute_run_in_background, run.id)
    if task is None:
        last = automation_service.last_run(db, automation)
        raise HTTPException(503, (last.reason if last and last.reason else "Nothing could take this right now."))
    db.refresh(automation)
    return automation_out(db, automation)


@router.delete("/{automation_id}", status_code=204)
def remove(automation_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    automation = _load(db, automation_id)
    db.delete(automation)
    db.commit()
