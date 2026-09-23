import uuid

import pytest

from adapters import ArtifactDraft, InvocationResult, ResultState
from app.domain.run_state import RunState
from app.domain.task_state import IllegalTransition, TaskState
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services.artifacts import is_known_type, resolve_path


def test_submit_creates_task_and_run(seeded):
    task = task_service.submit(seeded, "Compare three note-taking apps for a small practice")
    assert task.state == TaskState.QUEUED
    assert task.title.startswith("Compare three")
    assert len(task.runs) == 1
    assert task.runs[0].state == RunState.PENDING
    assert task.runs[0].provider.slug == "research"


def test_submit_rejects_empty_request(seeded):
    with pytest.raises(ValueError):
        task_service.submit(seeded, "   ")


def test_long_requests_get_a_short_title(seeded):
    words = "Compare " + " ".join(["option"] * 40)
    task = task_service.submit(seeded, words)
    assert len(task.title) <= 81 and task.title.endswith("…")
    assert task.original_request == words


def test_demo_router_sends_event_wording_to_event_demo(seeded):
    task = task_service.submit(seeded, "Allocate the participants for the spring conference")
    assert task.runs[0].provider.slug == "event-demo"


def test_example_task_completes_with_summary_and_artifacts(seeded):
    task = task_service.submit(seeded, "Allocate the participants for the spring conference")
    seeded.commit()
    run = task_service.execute_run(seeded, task.runs[0].id)
    seeded.refresh(task)
    assert run.state == RunState.COMPLETED
    assert task.state == TaskState.COMPLETED
    assert task.summary == "Done. 148 participants allocated. 7 need review."
    assert task.completed_at is not None
    types = {a.type for a in task.artifacts}
    assert types == {"structured", "deep_link"}
    link = next(a for a in task.artifacts if a.type == "deep_link")
    assert link.title == "Review in the events app"
    assert link.external_url.startswith("https://events.example/app/events/")


def test_research_stores_a_file_backed_artifact(seeded):
    task = task_service.submit(seeded, "Find the best three options")
    seeded.commit()
    task_service.execute_run(seeded, task.runs[0].id)
    seeded.refresh(task)
    note = task.artifacts[0]
    assert note.type == "note" and note.storage_path
    path = resolve_path(note)
    assert path is not None and path.read_text(encoding="utf-8").startswith("# Find the best three options")


def test_provider_exception_becomes_failed_task_not_crash(seeded, monkeypatch):
    import providers.research as research

    def boom(request, provider):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(research, "run", boom)
    task = task_service.submit(seeded, "Find something")
    seeded.commit()
    run = task_service.execute_run(seeded, task.runs[0].id)
    seeded.refresh(task)
    assert run.state == RunState.FAILED
    assert task.state == TaskState.FAILED
    assert "secret internal detail" not in (task.summary or "")


def test_needs_input_result_pauses_the_task(seeded, monkeypatch):
    import providers.research as research

    monkeypatch.setattr(
        research, "run", lambda request, provider: InvocationResult(state=ResultState.NEEDS_INPUT, summary="Which year?")
    )
    task = task_service.submit(seeded, "Find something")
    seeded.commit()
    task_service.execute_run(seeded, task.runs[0].id)
    seeded.refresh(task)
    assert task.state == TaskState.NEEDS_INPUT
    assert task.summary == "Which year?"
    assert task.completed_at is None


def test_unknown_artifact_type_is_stored_and_flagged(seeded, monkeypatch):
    import providers.research as research

    monkeypatch.setattr(
        research,
        "run",
        lambda request, provider: InvocationResult(
            state=ResultState.COMPLETED,
            summary="Done.",
            artifacts=[ArtifactDraft(type="hologram", title="A hologram", external_url="https://example.com/h/1")],
        ),
    )
    task = task_service.submit(seeded, "Find something")
    seeded.commit()
    task_service.execute_run(seeded, task.runs[0].id)
    seeded.refresh(task)
    [artifact] = task.artifacts
    assert artifact.type == "hologram"
    assert is_known_type(artifact.type) is False
    assert artifact.external_url == "https://example.com/h/1"


def test_cancel_open_task_and_not_completed_one(seeded):
    task = task_service.submit(seeded, "Find something")
    task_service.cancel_task(seeded, task)
    assert task.state == TaskState.CANCELLED
    assert task.runs[0].state == RunState.CANCELLED
    with pytest.raises(IllegalTransition):
        task_service.cancel_task(seeded, task)


def test_task_can_target_a_specific_provider(seeded):
    event_demo = provider_service.get_by_slug(seeded, "event-demo")
    task = task_service.submit(seeded, "Find something unrelated to events", provider=event_demo)
    assert task.runs[0].provider_id == event_demo.id


def test_missing_run_raises(seeded):
    with pytest.raises(LookupError):
        task_service.execute_run(seeded, uuid.uuid4())
