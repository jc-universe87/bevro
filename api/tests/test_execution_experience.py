"""From "start this" to a result: one task, told in plain words throughout.

Whatever runs the work and however many ways Bevro tries to reach the
provider, the person sees one task: starting, working (and on what, if the
provider says), trying another way, needing them, completed with its result
first, or couldn't complete - with what they can do next, and whether trying
again could repeat something the app already did.

Only the single "call one way in" step is scripted; fallback, recording,
finishing, status wording and the HTTP API are the real ones. Synthetic
providers only.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta

import pytest

from adapters import ArtifactDraft, FailureKind, InvocationResult, ResultState
from adapters.runtime import Credentials, CredentialStrategy, RuntimeKind
from app.connect.runtimes import http_runtime
from sqlalchemy.orm import sessionmaker

from app.models import ProviderRun, Task
from app.models._common import utcnow
from app.schemas.serialise import task_status
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.services import tasks as task_service


def _way(rid: str, display: str):
    return http_runtime(
        rid,
        kind=RuntimeKind.PROCESS,
        adapter={"kind": "http", "config": {"base_url": f"http://{rid}.invalid", "invoke": {"method": "POST", "path": "/task", "body": {"prompt": "{request}"}}, "response": {"text": "answer"}, "timeout_seconds": 3}},
        display_name=display,
        availability="ready",
        confidence="high",
        credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED),
        evidence=[],
    )


@pytest.fixture
def desk(seeded):
    """A synthetic agent Bevro reaches two ways."""
    ways = [_way("near", "Already running on this machine"), _way("far", "Connected over the network")]
    provider = provider_service.register_provider(
        seeded,
        {"name": "Jobs Desk", "capabilities": [{"id": "shortlist", "title": "Review opportunities"}], "adapter": ways[0].adapter, "runtimes": ways, "active_runtime": "near"},
    )
    seeded.commit()
    return provider


UNREACHABLE = "connect ECONNREFUSED 10.0.0.9:8080"


@pytest.fixture(autouse=True)
def own_sessions(db, monkeypatch):
    """Progress, fallback and heartbeats are written from their own sessions,
    as they are from any thread in production. Here each test is one open
    transaction, so those sessions share its connection to see it."""
    factory = sessionmaker(bind=db.connection(), autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(task_service, "get_sessionmaker", lambda: factory)
    return factory


@pytest.fixture
def script(monkeypatch):
    """What each call to one way in does, in the order Bevro makes them:
    a list of function(request, context) -> (result, artifacts). A way in
    with nothing scripted for it is not reached. Records which ways were
    tried, by display name."""
    plan: list = []
    plan_tried: list[str] = []

    def invoke_once(provider, runtime, request, context):
        plan_tried.append(runtime.display_name)
        step = plan.pop(0) if plan else None
        if step is None:
            return InvocationResult(state=ResultState.FAILED, error=UNREACHABLE, failure=FailureKind.PROVIDER_UNAVAILABLE), []
        return step(request, context)

    monkeypatch.setattr(runtime_service, "_invoke_once", invoke_once)
    plan_tried.clear()
    script_state["tried"] = plan_tried
    return plan


script_state: dict = {}


def unreachable(request, context):
    return InvocationResult(state=ResultState.FAILED, error=UNREACHABLE, failure=FailureKind.PROVIDER_UNAVAILABLE), []


def ok(summary="Three opportunities are waiting for a decision.", artifacts=()):
    return lambda request, context: (InvocationResult(state=ResultState.COMPLETED, summary=summary), list(artifacts))


def start(client, provider, text="Review my opportunities"):
    r = client.post("/api/tasks", json={"request": text, "provider_id": str(provider.id)})
    assert r.status_code == 201, r.text
    return r.json()


def detail(client, task_id):
    return client.get(f"/api/tasks/{task_id}").json()


# --------------------------------------------------------------------------- starting and working

def test_A_B_a_task_appears_at_once_as_starting_then_says_who_is_working(client, seeded, desk, script):
    seen = {}

    def working(request, context):
        # Mid-run, as the person would see it.
        with task_service.get_sessionmaker()() as db:
            seen["status"] = task_status(db.get(Task, uuid.UUID(request.task_id))).model_dump()
        return ok()(request, context)

    script.append(working)
    created = start(client, desk)
    # The answer to "start this" is the task itself, already there.
    assert created["status"]["kind"] == "starting" and created["status"]["headline"] == "Starting…"
    assert seen["status"]["kind"] == "working" and seen["status"]["headline"] == "Jobs Desk is working on this."
    assert seen["status"]["since"] is not None


def test_what_a_provider_says_it_is_doing_is_shown_and_nothing_else_is_made_up(client, seeded, desk, script):
    seen = {}

    def reporting(request, context):
        context.progress("Reviewing opportunities")
        with task_service.get_sessionmaker()() as db:
            seen["status"] = task_status(db.get(Task, uuid.UUID(request.task_id))).model_dump()
        return ok()(request, context)

    script.append(reporting)
    start(client, desk)
    assert seen["status"]["note"] == "Reviewing opportunities"
    # V: no invented percentages anywhere in what the person is told.
    assert not re.search(r"\d+\s*%", json.dumps(seen["status"], default=str))


def test_D_E_a_finished_task_says_so_with_its_text_first(client, seeded, desk, script):
    script.append(ok(artifacts=[ArtifactDraft(type="text", title="Answer", payload={"text": "Three are waiting."})]))
    task = detail(client, start(client, desk)["id"])
    assert task["state"] == "completed"
    assert task["status"] == {**task["status"], "kind": "completed", "label": "Completed", "headline": "Jobs Desk finished this.", "note": None}
    assert task["summary"] == "Three opportunities are waiting for a decision."
    assert task["artifacts"][0]["primary"] is True and task["artifacts"][0]["payload"] == {"text": "Three are waiting."}


def test_F_H_a_file_result_opens_through_bevro_and_no_place_on_disk_is_shown(client, seeded, desk, script):
    script.append(ok(
        artifacts=[ArtifactDraft(type="file", title="Shortlist", mime_type="text/csv", content=b"a,b\n", filename="shortlist.csv", metadata={"path": "/srv/private/agents/desk/shortlist.csv", "rows": 1, "made_in": "reports/shortlist.csv"})]
    ))
    task = detail(client, start(client, desk)["id"])
    [file] = task["artifacts"]
    assert file["title"] == "Shortlist" and file["content_url"] == f"/api/artifacts/{file['id']}/content"
    assert client.get(file["content_url"]).content == b"a,b\n"
    # The provider's own relative name for it may stay; where anything lives on a disk may not.
    assert file["metadata"] == {"rows": 1, "made_in": "reports/shortlist.csv"}
    text = client.get(f"/api/tasks/{task['id']}").text
    assert "/srv/" not in text and "storage_path" not in text


def test_G_several_results_put_the_one_to_read_first(client, seeded, desk, script):
    script.append(ok(
        artifacts=[
            ArtifactDraft(type="deep_link", title="Open in Jobs Desk", external_url="https://desk.example/app"),
            ArtifactDraft(type="file", title="Shortlist", content=b"x", filename="s.csv"),
            ArtifactDraft(type="report", title="Weekly review", payload={"text": "# Review"}),
        ]
    ))
    task = detail(client, start(client, desk)["id"])
    assert [(a["title"], a["primary"]) for a in task["artifacts"]] == [("Weekly review", True), ("Shortlist", False), ("Open in Jobs Desk", False)]


def test_a_provider_can_say_which_result_is_its_main_one(client, seeded, desk, script):
    script.append(ok(
        artifacts=[
            ArtifactDraft(type="report", title="Notes on the run", payload={"text": "..."}),
            ArtifactDraft(type="file", title="The shortlist", content=b"x", filename="s.csv", metadata={"primary": True}),
        ]
    ))
    task = detail(client, start(client, desk)["id"])
    assert task["artifacts"][0]["title"] == "The shortlist" and task["artifacts"][0]["primary"]


def test_J_a_result_that_couldn_t_all_be_kept_says_so(client, seeded, desk, script, monkeypatch):
    from app.services import artifacts as artifact_service

    def refuse(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(artifact_service, "store_draft", refuse)
    script.append(ok(artifacts=[ArtifactDraft(type="text", title="Answer", payload={"text": "x"})]))
    task = detail(client, start(client, desk)["id"])
    assert task["state"] == "completed" and task["status"]["note"] == "Part of the result couldn't be saved."
    assert "disk full" not in json.dumps(task)


# --------------------------------------------------------------------------- another way in

def test_I_J_while_bevro_tries_another_way_nothing_has_failed_and_then_it_is_done(client, seeded, desk, script):
    seen = {}

    def far(request, context):
        with task_service.get_sessionmaker()() as db:
            seen["status"] = task_status(db.get(Task, uuid.UUID(request.task_id))).model_dump()
            seen["state"] = db.get(Task, uuid.UUID(request.task_id)).state
        return ok()(request, context)

    script.extend([unreachable, far])  # the first way isn't reached; the second is
    task = detail(client, start(client, desk)["id"])
    assert seen["state"] == "working" and seen["status"]["kind"] == "working"
    assert seen["status"]["headline"] == "Trying another way to reach Jobs Desk…"
    assert task["status"]["kind"] == "completed"
    assert task["status"]["note"] == "It worked after Bevro tried another way to reach it."
    first, second = script_state["tried"]
    assert task["runs"][-1]["tried"] == [{"way": first, "outcome": "Couldn't reach it"}, {"way": second, "outcome": "Worked"}]


def test_K_when_every_way_fails_it_is_one_plain_failure_and_safe_to_try_again(client, seeded, desk, script):
    task = detail(client, start(client, desk)["id"])  # neither way is reached
    assert task["state"] == "failed"
    assert task["status"] == {**task["status"], "kind": "failed", "label": "Couldn't reach it", "headline": "I couldn't reach Jobs Desk."}
    failure = task["runs"][-1]["failure"]
    # Bevro can't reach it from here now, so checking it comes before trying again.
    assert [a["kind"] for a in failure["actions"]] == ["test_connection", "open_app"]
    # It never had the request, so trying again can't repeat anything.
    assert failure["may_repeat"] is False
    assert [t["outcome"] for t in task["runs"][-1]["tried"]] == ["Couldn't reach it", "Couldn't reach it"]
    # The technical reason stays out of what the person is told.
    assert UNREACHABLE not in json.dumps(task["status"]) + json.dumps(failure) + json.dumps(task["runs"][-1]["tried"])


def test_an_app_that_started_and_failed_is_not_blamed_on_reaching_it_and_warns_before_repeating(client, seeded, desk, script):
    script.append(lambda r, c: (InvocationResult(state=ResultState.FAILED, error="HTTP 500 from /task", failure=FailureKind.INVOCATION_FAILED), []))
    task = detail(client, start(client, desk)["id"])
    failure = task["runs"][-1]["failure"]
    assert failure["message"] == "Jobs Desk started the work but couldn't complete it."
    assert failure["may_repeat"] is True
    assert "HTTP 500" not in json.dumps(task["status"]) + json.dumps(failure)
    # The work itself failed: another way in would do the same, so none is tried.
    assert len(task["runs"][-1]["tried"]) == 1


def test_a_crash_in_bevro_is_bevro_s_and_is_said_so(seeded, desk, monkeypatch):
    def crash(*a, **k):
        raise RuntimeError("boom")

    task = task_service.submit(seeded, "Review my opportunities", provider=desk)
    seeded.commit()
    monkeypatch.setattr(task_service, "execute", crash)
    task_service.execute_run_in_background(task.runs[0].id)  # the real entry point, with its safety net
    seeded.expire_all()
    task = seeded.get(Task, task.id)
    assert task_status(task).headline == "Bevro hit a problem while handling this."
    from app.schemas.serialise import failure_out

    assert failure_out(task.runs[-1]).category == "bevro_error" and "boom" not in str(failure_out(task.runs[-1]))


# --------------------------------------------------------------------------- trying again

def test_O_P_W_trying_again_is_the_same_task_with_another_run_and_only_once(client, seeded, desk, script):
    script.append(lambda r, c: (InvocationResult(state=ResultState.FAILED, error="x", failure=FailureKind.INVOCATION_FAILED), []))
    first = start(client, desk)
    failed = detail(client, first["id"])
    # It may have acted on the request already: the page asks before sending it again.
    assert failed["state"] == "failed" and failed["runs"][-1]["failure"]["may_repeat"] is True
    script.append(ok())
    r = client.post(f"/api/tasks/{first['id']}/retry")
    assert r.status_code == 200
    # A second press (or a refresh that repeats it) doesn't start a third run.
    assert client.post(f"/api/tasks/{first['id']}/retry").status_code == 409
    task = detail(client, first["id"])
    assert task["state"] == "completed" and len(task["runs"]) == 2
    assert [t["id"] for t in client.get("/api/tasks").json()].count(first["id"]) == 1
    assert seeded.query(Task).count() == 1


def test_R_work_by_an_app_since_removed_stays_readable_and_is_not_retried(client, seeded, desk, script):
    task_id = start(client, desk)["id"]
    assert client.delete(f"/api/providers/{desk.id}").status_code == 204
    task = detail(client, task_id)
    assert task["provider"]["name"] == "Jobs Desk" and task["provider"]["removed"] is True
    assert task["status"]["kind"] == "failed"
    r = client.post(f"/api/tasks/{task_id}/retry")
    assert r.status_code == 409 and "was removed from Bevro" in r.json()["detail"]


# --------------------------------------------------------------------------- quiet, stale, stopped

def _running(seeded, provider, *, execution="inline", heard_ago=timedelta(0)):
    task = Task(title="Review", original_request="Review my opportunities", state="working")
    seeded.add(task)
    seeded.flush()
    run = ProviderRun(task_id=task.id, provider_id=provider.id, provider_name=provider.name, provider_slug=provider.slug, state="running", execution=execution, started_at=utcnow() - heard_ago, heartbeat_at=utcnow() - heard_ago)
    seeded.add(run)
    seeded.commit()
    seeded.refresh(task)
    return task, run


def test_a_quiet_run_is_said_to_be_quiet_not_failed(seeded, desk):
    task, _run = _running(seeded, desk, heard_ago=timedelta(seconds=60))
    status = task_status(task)
    assert status.kind == "working" and status.quiet and status.note == "Bevro hasn't had an update recently."


def test_work_nobody_is_driving_any_more_ends_honestly(seeded, desk):
    task, run = _running(seeded, desk, heard_ago=timedelta(minutes=10))
    assert task_service.reap_stale_runs(seeded) == 1
    seeded.refresh(task)
    status = task_status(task)
    assert status.kind == "failed" and status.headline == "Bevro lost contact with Jobs Desk while it was working on this."


def test_work_that_never_started_says_so_and_is_safe_to_try_again(seeded, desk):
    task = Task(title="Review", original_request="Review", state="queued")
    seeded.add(task)
    seeded.flush()
    run = ProviderRun(task_id=task.id, provider_id=desk.id, provider_name=desk.name, provider_slug=desk.slug, state="pending", execution="inline")
    run.created_at = utcnow() - timedelta(minutes=10)
    seeded.add(run)
    seeded.commit()
    assert task_service.reap_stale_runs(seeded) == 1
    seeded.refresh(task)
    from app.schemas.serialise import failure_out

    assert task_status(task).headline == "I couldn't start this with Jobs Desk."
    assert failure_out(run).may_repeat is False


def test_work_waiting_for_the_helper_waits_and_says_what_for(seeded, desk):
    task = Task(title="Review", original_request="Review", state="queued")
    seeded.add(task)
    seeded.flush()
    run = ProviderRun(task_id=task.id, provider_id=desk.id, provider_name=desk.name, provider_slug=desk.slug, state="pending", execution="background")
    run.created_at = utcnow() - timedelta(minutes=10)
    seeded.add(run)
    seeded.commit()
    assert task_service.reap_stale_runs(seeded) == 0
    seeded.refresh(task)
    status = task_status(task)
    assert status.kind == "starting" and status.note == "Waiting for the helper on this computer to pick this up."


def test_cancel_is_offered_only_where_bevro_can_really_stop_it(client, seeded, desk):
    task, _run = _running(seeded, desk)  # a request already sent over the network
    assert task_status(task).can_cancel is False
    r = client.post(f"/api/tasks/{task.id}/cancel")
    assert r.status_code == 409 and "can't be stopped" in r.json()["detail"]
    queued = Task(title="Later", original_request="Later", state="queued")
    seeded.add(queued)
    seeded.commit()
    assert task_status(queued).can_cancel is True
    assert client.post(f"/api/tasks/{queued.id}/cancel").json()["status"]["kind"] == "stopped"


def test_S_recent_lists_each_task_in_plain_words(client, seeded, desk, script):
    script.append(ok(artifacts=[ArtifactDraft(type="text", title="Answer", payload={"text": "x"})]))
    start(client, desk)
    [row] = client.get("/api/tasks").json()
    assert row["status"]["label"] == "Completed" and row["provider"]["name"] == "Jobs Desk" and row["results"] == 1
    assert not re.search(r"runtime|adapter|provider run|heartbeat|attempt|http|exit code", json.dumps(row["status"]), re.I)


def test_G_bevro_failing_after_the_app_answered_is_bevro_s_failure(seeded, desk, script, monkeypatch):
    task = task_service.submit(seeded, "Review my opportunities", provider=desk)
    seeded.commit()
    script.append(ok())

    def handling_breaks(*a, **k):
        raise RuntimeError("could not file the result")

    monkeypatch.setattr(task_service, "finish_run", handling_breaks)
    task_service.execute_run_in_background(task.runs[0].id)
    seeded.expire_all()
    task = seeded.get(Task, task.id)
    from app.schemas.serialise import failure_out

    failure = failure_out(task.runs[-1])
    assert failure.category == "bevro_error" and failure.message == "Bevro hit a problem while handling this."
    assert failure.may_repeat is True  # the app did the work; doing it again may repeat it
    assert "credential" not in task_status(task).headline
