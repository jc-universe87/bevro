"""The task service: Bevro's own record of work, from request to result.

submit()        creates the Task and its first ProviderRun and hands back quickly.
answer_input()  fills in what Bevro had to ask for (e.g. which project).
begin_run()     marks a run as running (API thread or worker, whoever takes it).
finish_run()    records an adapter's result: artifacts, states, summary.
execute_run()   begin + invoke + finish, for quick "inline" providers.

Runs of "background" providers are left pending in the database for the
worker process (app/worker.py) to pick up; they may take minutes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from adapters import FailureKind, InvocationContext, InvocationRequest, InvocationResult, ResultState, get_adapter
from adapters.base import NotSupported
from app.config import get_settings
from app.db import get_sessionmaker
from app.domain.run_state import RUN_TERMINAL, RunState
from app.domain.task_state import IllegalTransition, TaskState, assert_transition, can_retry, is_terminal
from app.models import Provider, ProviderRun, Task
from app.models._common import utcnow
from app.services import artifacts as artifact_service
from app.services import workspaces as workspace_service
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.routing import RoutingDecision, RoutingSource, get_router
from app.routing.deterministic import unavailable_message
from app.routing.prompt import ROUTER_PROMPT_VERSION
from app.routing.validate import InvalidDecision, validate_decision
from app.services.providers import is_available
from app.services.secrets import SecretStore

log = logging.getLogger("bevro.tasks")

TITLE_MAX = 80
MAX_STEPS = 30
WORKSPACE_QUESTION = "Which project should I work on?"


def title_from_request(request: str) -> str:
    text = " ".join(request.split())
    if len(text) <= TITLE_MAX:
        return text
    cut = text[:TITLE_MAX].rsplit(" ", 1)[0]
    return cut + "…"


def transition(task: Task, target: TaskState) -> None:
    assert_transition(TaskState(task.state), target)
    task.state = target
    if is_terminal(target):
        task.completed_at = utcnow()


class NoProviderAvailable(Exception):
    """Nothing can take this request. `reason` is a short machine code for the UI."""

    def __init__(self, message: str, reason: str = "unavailable") -> None:
        super().__init__(message)
        self.reason = reason


NO_PROVIDER_MESSAGE = "I don't have a connected provider for that yet."


class InvalidInput(Exception):
    pass


def _adapter_for(provider: Provider):
    """The active runtime's adapter, or None when nothing can run it."""
    try:
        rt = runtime_service.active_runtime(provider)
        return runtime_service.adapter_for(rt) if rt else get_adapter(str(provider.adapter.get("kind", "")))
    except NotSupported:
        return None


# --------------------------------------------------------------------------- submit

def submit(db: Session, request: str, *, provider: Provider | None = None, input: dict[str, Any] | None = None, trusted: bool = False, title: str | None = None) -> Task:
    """Create a task and its first run. Caller commits. Does not execute.

    `trusted` is for work Bevro gives itself (building a connection, say): the
    input is taken as built rather than filtered, because it came from the
    server, not from a browser. `title` names such work plainly.
    """
    text = request.strip()
    if not text:
        raise ValueError("request is empty")
    chosen, decision, routing_meta = _choose_provider(db, text, provider)
    adapter = _adapter_for(chosen)

    task = Task(title=(title or title_from_request(text))[:TITLE_MAX], original_request=text, state=TaskState.CREATED, routing=routing_meta)
    db.add(task)
    db.flush()
    rt = runtime_service.active_runtime(chosen)
    run = ProviderRun(
        task_id=task.id,
        provider_id=chosen.id,
        state=RunState.PENDING,
        input=dict(input or {}) if trusted else _safe_input(input),
        execution=runtime_service.execution_of(chosen, provider_service.worker_seen_recently(db)),
        meta={"runtime": {"id": rt.id, "kind": rt.kind.value}} if rt else {},
    )
    db.add(run)
    db.flush()

    if "workspace" in runtime_service.requires_of(chosen) and not (trusted and run.input.get("workspace_id")):
        # Bevro decides whether to ask: the router's opinion is advisory here.
        _resolve_workspace_or_ask(db, task, run, text)
    elif trusted and run.input.get("workspace_id"):
        ws = workspace_service.get_workspace(db, run.input["workspace_id"])
        if ws is None:
            raise InvalidInput("That workspace is not available.")
        _grant_workspace(run, ws)
    elif decision.needs_input and decision.input_request and decision.input_request.kind == "question":
        _ask_question(task, run, decision.input_request.prompt)
    if task.state == TaskState.CREATED:
        transition(task, TaskState.QUEUED)
    db.flush()
    return task


def _choose_provider(db: Session, text: str, explicit: Provider | None) -> tuple[Provider, RoutingDecision, dict[str, Any]]:
    """Explicit choice bypasses routing; otherwise ask the router and validate."""
    if explicit is not None:
        if not is_available(explicit):
            raise NoProviderAvailable(f"{explicit.name} isn't available on this installation right now.")
        decision = RoutingDecision(selected_provider_ids=[explicit.slug], routing_source=RoutingSource.EXPLICIT, confidence=1.0)
        return explicit, decision, decision.to_metadata(router_version=ROUTER_PROMPT_VERSION)

    router = get_router()
    decision = router.route(db, text)
    try:
        validated = validate_decision(db, decision)
    except InvalidDecision as exc:
        log.warning("routing decision rejected: %s", exc)
        raise NoProviderAvailable(NO_PROVIDER_MESSAGE, reason="no_provider") from None
    meta = decision.to_metadata(
        router_version=ROUTER_PROMPT_VERSION,
        backend=getattr(getattr(router, "model", None), "name", None),
        fallback_reason=getattr(router, "last_fallback_reason", None),
    )
    if validated.provider is None:
        log.info("routing: no suitable provider (%s)", decision.reason)
        raise NoProviderAvailable(unavailable_message(decision) or NO_PROVIDER_MESSAGE, reason="no_provider")
    return validated.provider, decision, meta


def _ask_question(task: Task, run: ProviderRun, prompt: str) -> None:
    question = " ".join(prompt.split())[:200] or "Could you tell me a little more?"
    run.input_request = {"question": question, "kind": "text", "field": "answer", "options": []}
    run.state = RunState.NEEDS_INPUT
    transition(task, TaskState.NEEDS_INPUT)
    task.summary = question


def _safe_input(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Only plain, known keys from outside. Never a path."""
    out: dict[str, Any] = {}
    if raw and isinstance(raw.get("workspace_id"), str):
        out["workspace_id"] = raw["workspace_id"]
    return out


def _resolve_workspace_or_ask(db: Session, task: Task, run: ProviderRun, text: str) -> None:
    ws = None
    if run.input.get("workspace_id"):
        ws = workspace_service.resolve_workspace(db, run.input["workspace_id"])
        if ws is None:
            raise InvalidInput("That project is not available.")
    ws = ws or workspace_service.infer_from_text(db, text)
    if ws is not None:
        _grant_workspace(run, ws)
        return
    options = workspace_service.list_workspaces(db)
    if not options:
        run.state = RunState.FAILED
        run.error_summary = "No project is set up for this kind of work yet."
        run.completed_at = utcnow()
        transition(task, TaskState.FAILED)
        task.summary = run.error_summary
        return
    run.input_request = {
        "question": WORKSPACE_QUESTION,
        "kind": "choice",
        "field": "workspace_id",
        "options": workspace_service.as_choice_options(options),
    }
    run.state = RunState.NEEDS_INPUT
    transition(task, TaskState.NEEDS_INPUT)
    task.summary = WORKSPACE_QUESTION


def _grant_workspace(run: ProviderRun, ws) -> None:
    run.input = {**run.input, "workspace_id": str(ws.id), "permissions": list(ws.permissions)}


def answer_input(db: Session, task: Task, value: str) -> ProviderRun:
    """Answer the question a run is waiting on. Caller commits."""
    run = next((r for r in reversed(task.runs) if r.input_request), None)
    if run is None or task.state != TaskState.NEEDS_INPUT:
        raise InvalidInput("This task isn't waiting for anything.")
    req = run.input_request or {}
    allowed = {o.get("value") for o in req.get("options", [])}
    if req.get("kind") == "choice" and value not in allowed:
        raise InvalidInput("That isn't one of the options.")
    if req.get("kind") == "text" and not value.strip():
        raise InvalidInput("Please type an answer.")
    field = str(req.get("field") or "answer")
    if field == "workspace_id":
        ws = workspace_service.resolve_workspace(db, value)
        if ws is None:
            raise InvalidInput("That project is not available.")
        _grant_workspace(run, ws)
    else:
        run.input = {**run.input, field: value.strip()[:500]}
    run.input_request = None
    run.state = RunState.PENDING
    task.summary = None
    transition(task, TaskState.QUEUED)
    db.flush()
    return run


# --------------------------------------------------------------------------- execution

_RESULT_TO_TASK = {
    ResultState.COMPLETED: TaskState.COMPLETED,
    ResultState.FAILED: TaskState.FAILED,
    ResultState.NEEDS_INPUT: TaskState.NEEDS_INPUT,
    ResultState.NEEDS_APPROVAL: TaskState.NEEDS_APPROVAL,
    ResultState.RUNNING: TaskState.WAITING,
    ResultState.CANCELLED: TaskState.CANCELLED,
}
_RESULT_TO_RUN = {
    ResultState.COMPLETED: RunState.COMPLETED,
    ResultState.FAILED: RunState.FAILED,
    ResultState.NEEDS_INPUT: RunState.NEEDS_INPUT,
    ResultState.NEEDS_APPROVAL: RunState.NEEDS_APPROVAL,
    ResultState.RUNNING: RunState.RUNNING,
    ResultState.CANCELLED: RunState.CANCELLED,
}


def load_run(db: Session, run_id: uuid.UUID) -> ProviderRun:
    run = db.get(ProviderRun, run_id, options=[selectinload(ProviderRun.task), selectinload(ProviderRun.provider)])
    if run is None:
        raise LookupError(f"run {run_id} not found")
    return run


def begin_run(db: Session, run: ProviderRun, *, worker_id: str | None = None) -> None:
    """Mark a run (and its task) as working. Commits so the UI can see it."""
    run.state = RunState.RUNNING
    run.started_at = utcnow()
    run.worker_id = worker_id
    run.heartbeat_at = utcnow()
    run.progress = {"phase": "Working…", "steps": []}
    if run.task.state != TaskState.WORKING and not is_terminal(TaskState(run.task.state)):
        transition(run.task, TaskState.WORKING)
    db.commit()


def build_request(db: Session, run: ProviderRun, *, secret_store: SecretStore | None = None) -> InvocationRequest:
    """Everything an adapter gets. Workspace details are resolved here, server-side."""
    provider, task = run.provider, run.task
    data: dict[str, Any] = dict(run.input)
    if data.get("workspace_id"):
        ws = workspace_service.get_workspace(db, data["workspace_id"])
        if ws is not None:
            data["workspace"] = workspace_service.to_input(ws)
            data.setdefault("permissions", list(ws.permissions))
    settings = get_settings()
    data["exclude_paths"] = [settings.artifact_dir, settings.log_dir]
    # Bevro-side storage for providers that need somewhere to run. Never the provider's own project.
    data["integration_dir"] = f"{settings.integrations_dir.rstrip('/')}/{provider.id}"
    secrets = secret_store.resolve(db, provider.id) if secret_store else {}
    return InvocationRequest(task_id=str(task.id), run_id=str(run.id), request=task.original_request, input=data, secrets=secrets)


def record_progress(run_id: uuid.UUID, text: str) -> None:
    """Persist a short human phase for a run. Own session: safe from any thread."""
    text = " ".join(str(text).split())[:120]
    if not text:
        return
    db = get_sessionmaker()()
    try:
        run = db.get(ProviderRun, run_id)
        if run is None:
            return
        progress = dict(run.progress or {})
        steps = list(progress.get("steps") or [])
        if not steps or steps[-1] != text:
            steps.append(text)
        run.progress = {"phase": text, "steps": steps[-MAX_STEPS:]}
        run.heartbeat_at = utcnow()
        db.commit()
    finally:
        db.close()


def is_cancel_requested(run_id: uuid.UUID) -> bool:
    db = get_sessionmaker()()
    try:
        return bool(db.scalar(select(ProviderRun.cancel_requested).where(ProviderRun.id == run_id)))
    finally:
        db.close()


def heartbeat(run_id: uuid.UUID, worker_id: str | None = None) -> None:
    db = get_sessionmaker()()
    try:
        run = db.get(ProviderRun, run_id)
        if run is not None:
            run.heartbeat_at = utcnow()
            if worker_id:
                run.worker_id = worker_id
            db.commit()
    finally:
        db.close()


def finish_run(db: Session, run: ProviderRun, result: InvocationResult, artifacts: list | None = None) -> ProviderRun:
    """Record what came back. Commits. `artifacts` are the normalised drafts from
    the runtime's collect_artifacts(); without them, what the result carried."""
    task = run.task
    for draft in (artifacts if artifacts is not None else result.artifacts):
        try:
            artifact_service.store_draft(db, task.id, run.id, draft)
        except Exception:  # noqa: BLE001
            log.exception("could not store artifact %r from %s", draft.title, run.provider.slug)

    if run.cancel_requested and result.state != ResultState.CANCELLED:
        result = result.model_copy(update={"state": ResultState.CANCELLED, "error": "The task was stopped before completion.", "summary": None})
    elif result.state == ResultState.CANCELLED and not run.cancel_requested:
        # Stopped by a timeout or a worker shutdown, not by the user: that is a failure, honestly reported.
        result = result.model_copy(update={"state": ResultState.FAILED, "error": result.error or "The task was stopped before completion.", "summary": None})

    run.state = _RESULT_TO_RUN[result.state]
    run.result_summary = result.summary
    run.error_summary = result.error
    run.external_ref = result.external_ref
    if result.metadata:
        meta = dict(result.metadata)
        # Health belongs to the provider's runtimes, not to this run.
        runtime_service.apply_health(run.provider, meta.pop(runtime_service.META_HEALTH, {}))
        run.meta = {**(run.meta or {}), **meta}
    if result.state == ResultState.FAILED:
        run.meta = {**(run.meta or {}), "failure": (result.failure or FailureKind.INVOCATION_FAILED).value}
    elif result.state == ResultState.CANCELLED:
        run.meta = {**(run.meta or {}), "failure": FailureKind.CANCELLED.value}
    if run.state in RUN_TERMINAL:
        run.completed_at = utcnow()
        run.progress = {**(run.progress or {}), "phase": None}

    target = _RESULT_TO_TASK[result.state]
    if not is_terminal(TaskState(task.state)):
        try:
            if task.state != target:
                transition(task, target)
        except IllegalTransition:
            log.warning("task %s cannot move from %s to %s; marking failed", task.id, task.state, target)
            transition(task, TaskState.FAILED)
        task.summary = result.summary or result.error
    elif task.state == TaskState.CANCELLED:
        task.summary = task.summary or result.error or "Cancelled."
    db.commit()
    db.refresh(run)
    return run


def _failed(message: str, failure: FailureKind = FailureKind.INVOCATION_FAILED) -> InvocationResult:
    return InvocationResult(state=ResultState.FAILED, error=message, failure=failure)


def execute_run(db: Session, run_id: uuid.UUID, *, secret_store: SecretStore | None = None, context: InvocationContext | None = None) -> ProviderRun:
    """Drive one pending run to an outcome in this process."""
    run = load_run(db, run_id)
    begin_run(db, run)
    request = build_request(db, run, secret_store=secret_store)
    result, artifacts = execute(run.provider, request, context, execution=run.execution)
    return finish_run(db, run, result, artifacts)


def execute(provider: Provider, request: InvocationRequest, context: InvocationContext | None = None, *, execution: str | None = None) -> tuple[InvocationResult, list]:
    """Run one piece of work through the provider's best available runtime,
    falling back if a runtime turns out to be down. Which runtime ran it, the
    attempts it took and the health they earned ride on the result's metadata;
    `finish_run` files them. The transport is the runtime layer's business."""
    return runtime_service.execute(provider, request, context, execution=execution)


def invoke_adapter(provider: Provider, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult:
    """Older entry point: invoke without the artifact step (tests)."""
    result, artifacts = runtime_service.execute(provider, request, context)
    return result.model_copy(update={"artifacts": artifacts})


def execute_run_in_background(run_id: uuid.UUID) -> None:
    """Entry point for FastAPI BackgroundTasks (inline providers only)."""
    db = get_sessionmaker()()
    try:
        store = None
        try:
            store = SecretStore()
        except RuntimeError:
            log.warning("BEVRO_SECRET_KEY not set; running without provider secrets")
        execute_run(db, run_id, secret_store=store)
    except Exception:  # noqa: BLE001
        log.exception("background run %s failed", run_id)
        db.rollback()
        mark_failed(db, run_id, "Something went wrong while running this.")
    finally:
        db.close()


def mark_failed(db: Session, run_id: uuid.UUID, message: str, failure: FailureKind = FailureKind.INVOCATION_FAILED) -> None:
    run = db.get(ProviderRun, run_id, options=[selectinload(ProviderRun.task)])
    if run is None:
        return
    run.state = RunState.FAILED
    run.error_summary = message
    run.completed_at = utcnow()
    run.progress = {**(run.progress or {}), "phase": None}
    run.meta = {**(run.meta or {}), "failure": failure.value}
    if not is_terminal(TaskState(run.task.state)):
        try:
            transition(run.task, TaskState.FAILED)
        except IllegalTransition:
            run.task.state = TaskState.FAILED
        run.task.summary = message
    db.commit()


def reap_stale_runs(db: Session) -> int:
    """Runs whose worker stopped reporting are failed honestly instead of spinning forever."""
    cutoff = utcnow() - timedelta(seconds=get_settings().worker_stale_seconds)
    stale = db.scalars(
        select(ProviderRun).where(
            ProviderRun.execution == "background",
            ProviderRun.state == RunState.RUNNING,
            (ProviderRun.heartbeat_at.is_(None)) | (ProviderRun.heartbeat_at < cutoff),
        )
    ).all()
    for run in stale:
        log.warning("run %s lost its worker; marking failed", run.id)
        mark_failed(db, run.id, "The task was stopped before completion.", FailureKind.PROVIDER_UNAVAILABLE)
    return len(stale)


# --------------------------------------------------------------------------- retry

def retry_task(db: Session, task: Task) -> ProviderRun:
    """Try a failed task again with the same provider and the same request.

    Deliberately the person's action, not a transition an agent can take: the
    task is reopened, a fresh run is queued, and the earlier run stays on the
    record. Caller commits.
    """
    if not can_retry(TaskState(task.state)):
        raise InvalidInput("This task isn't something Bevro can try again.")
    last = task.runs[-1] if task.runs else None
    if last is None:
        raise InvalidInput("This task has nothing to retry.")
    provider = last.provider
    if not provider.enabled or not is_available(provider):
        raise NoProviderAvailable(f"{provider.name} isn't available on this installation right now.")
    adapter = _adapter_for(provider)
    if adapter is None:
        raise NoProviderAvailable(f"{provider.name} can't be run on this installation.", reason="no_provider")
    rt = runtime_service.active_runtime(provider)
    run = ProviderRun(
        task_id=task.id,
        provider_id=provider.id,
        state=RunState.PENDING,
        input={k: v for k, v in (last.input or {}).items() if k in ("workspace_id", "permissions", "answer")},
        execution=runtime_service.execution_of(provider, provider_service.worker_seen_recently(db)),
        meta={"runtime": {"id": rt.id, "kind": rt.kind.value}} if rt else {},
    )
    task.runs.append(run)
    task.state = TaskState.QUEUED
    task.completed_at = None
    task.summary = None
    task.routing = {**(task.routing or {}), "retried": (task.routing or {}).get("retried", 0) + 1}
    db.flush()
    return run


# --------------------------------------------------------------------------- cancel / queries

def cancel_task(db: Session, task: Task) -> Task:
    transition(task, TaskState.CANCELLED)
    for run in task.runs:
        if run.state in RUN_TERMINAL:
            continue
        if run.execution == "background" and run.state == RunState.RUNNING:
            # The worker owns the process; it will stop it and finish the run.
            run.cancel_requested = True
        else:
            run.state = RunState.CANCELLED
            run.input_request = None
            run.completed_at = utcnow()
    task.summary = "Cancelled."
    return task


def list_tasks(db: Session, *, query: str | None = None, state: str | None = None, limit: int = 100) -> list[Task]:
    stmt = (
        select(Task)
        .options(selectinload(Task.runs).selectinload(ProviderRun.provider))
        .order_by(Task.created_at.desc())
        .limit(limit)
    )
    if state:
        stmt = stmt.where(Task.state == state)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(Task.title.ilike(like) | Task.original_request.ilike(like) | Task.summary.ilike(like))
    return list(db.scalars(stmt))


def get_task(db: Session, task_id: uuid.UUID) -> Task | None:
    return db.get(
        Task,
        task_id,
        options=[selectinload(Task.runs).selectinload(ProviderRun.provider), selectinload(Task.artifacts)],
    )
