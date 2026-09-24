from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.task_state import IllegalTransition
from app.models import Provider, Task
from app.schemas.serialise import task_detail, task_out
from app.schemas.tasks import InputAnswer, TaskDetail, TaskOut, TaskSubmit
from app.services import tasks as task_service
from app.services import workspaces as workspace_service
from app.services.secrets import SecretStore

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _detail(db: Session, task: Task) -> TaskDetail:
    workspaces = {str(w.id): w for w in workspace_service.list_workspaces(db, enabled_only=False)}
    # Names of stored secrets per provider (never values): lets a failed run say "credential missing".
    secret_names: dict[str, list[str]] = {}
    try:
        store = SecretStore()
    except RuntimeError:
        store = None
    if store is not None:
        for run in task.runs:
            if run.provider_id is None:
                continue  # the agent has been removed; there are no secrets to speak of
            key = str(run.provider_id)
            if key not in secret_names:
                secret_names[key] = store.names(db, run.provider_id)
    return task_detail(task, workspaces, secret_names)


def _schedule_inline(task: Task, background: BackgroundTasks) -> None:
    """Quick providers run in the API process. Background ones wait for the worker."""
    run = task.runs[-1] if task.runs else None
    if run is not None and run.execution == "inline" and run.state == "pending":
        background.add_task(task_service.execute_run_in_background, run.id)


@router.post("", response_model=TaskDetail, status_code=201)
def submit_task(body: TaskSubmit, background: BackgroundTasks, db: Session = Depends(get_db)) -> TaskDetail:
    provider = None
    if body.provider_id is not None:
        provider = db.get(Provider, body.provider_id)
        if provider is None or not provider.enabled:
            raise HTTPException(404, "That agent is not available.")
    try:
        task = task_service.submit(db, body.request, provider=provider, input={"workspace_id": body.workspace_id} if body.workspace_id else None)
    except task_service.NoProviderAvailable as exc:
        raise HTTPException(503, {"message": str(exc), "reason": exc.reason}) from None
    except task_service.InvalidInput as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    db.commit()
    task = task_service.get_task(db, task.id)
    assert task is not None
    _schedule_inline(task, background)
    return _detail(db, task)


@router.get("", response_model=list[TaskOut])
def list_tasks(
    q: str | None = Query(default=None, max_length=200),
    state: str | None = Query(default=None, max_length=32),
    db: Session = Depends(get_db),
) -> list[TaskOut]:
    return [task_out(t) for t in task_service.list_tasks(db, query=q, state=state)]


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: uuid.UUID, db: Session = Depends(get_db)) -> TaskDetail:
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(404, "Task not found.")
    return _detail(db, task)


@router.post("/{task_id}/input", response_model=TaskDetail)
def answer_input(task_id: uuid.UUID, body: InputAnswer, background: BackgroundTasks, db: Session = Depends(get_db)) -> TaskDetail:
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(404, "Task not found.")
    try:
        task_service.answer_input(db, task, body.value)
    except task_service.InvalidInput as exc:
        raise HTTPException(422, str(exc)) from None
    except IllegalTransition:
        raise HTTPException(409, "This task can't continue from here.") from None
    db.commit()
    task = task_service.get_task(db, task.id)
    assert task is not None
    _schedule_inline(task, background)
    return _detail(db, task)


@router.post("/{task_id}/retry", response_model=TaskDetail)
def retry_task(task_id: uuid.UUID, background: BackgroundTasks, db: Session = Depends(get_db)) -> TaskDetail:
    """Try a failed task again: same request, same provider, a fresh run on the same task."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(404, "Task not found.")
    try:
        task_service.retry_task(db, task)
    except task_service.InvalidInput as exc:
        raise HTTPException(409, str(exc)) from None
    except task_service.NoProviderAvailable as exc:
        raise HTTPException(503, {"message": str(exc), "reason": exc.reason}) from None
    db.commit()
    task = task_service.get_task(db, task.id)
    assert task is not None
    _schedule_inline(task, background)
    return _detail(db, task)


@router.delete("", status_code=200)
def clear_history(db: Session = Depends(get_db)) -> dict[str, int]:
    """Forget all finished work. Agents, scheduled work and settings stay."""
    removed = task_service.clear_history(db)
    db.commit()
    return {"removed": removed}


@router.delete("/{task_id}", status_code=204)
def remove_task(task_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Remove one task and its results. The agent that did it is untouched."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(404, "Task not found.")
    try:
        task_service.delete_task(db, task)
    except task_service.InvalidInput as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()


@router.post("/{task_id}/cancel", response_model=TaskDetail)
def cancel_task(task_id: uuid.UUID, db: Session = Depends(get_db)) -> TaskDetail:
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(404, "Task not found.")
    try:
        task_service.cancel_task(db, task)
    except IllegalTransition:
        raise HTTPException(409, "This task can no longer be cancelled.") from None
    db.commit()
    db.refresh(task)
    return _detail(db, task)
