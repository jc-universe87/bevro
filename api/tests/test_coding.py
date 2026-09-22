"""Claude Code as a provider: workspaces, execution lifecycle, safety.

All of this runs against tests/fake_claude.py, never the real CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from adapters import HealthResult, InvocationContext, InvocationRequest, ProviderSpec, ResultState, get_adapter
from adapters import workspace as ws_mod
from adapters.claude_code import build_command, strip_noise
from app.domain.run_state import RunState
from app.domain.task_state import TaskState
from app.models import Workspace
from app.models._common import utcnow
from app.services import providers as provider_service
from app.services import tasks as task_service
from app.services import workspaces as workspace_service
from app.worker import acquire

FAKE = str(Path(__file__).parent / "fake_claude.py")


# ----------------------------------------------------------------------------- fixtures

def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small git repository with one already-dirty file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# demo\n")
    (repo / "app.py").write_text("print('hi')\n")
    (repo / "dirty.txt").write_text("committed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "dirty.txt").write_text("committed\nuser edit before the task\n")
    return repo


def mark_available(db) -> None:
    """What the worker does on start: report that Claude Code can run here."""
    provider_service.record_availability(db, provider_service.get_by_slug(db, "claude-code"), HealthResult(ok=True, state="available"))


@pytest.fixture
def workspace(seeded, repo: Path) -> Workspace:
    mark_available(seeded)
    ws = Workspace(slug="demo", name="Demo", path=str(repo), description="test repo", enabled=True, permissions=["read", "write", "run_commands"])
    seeded.add(ws)
    seeded.flush()
    return ws


@pytest.fixture
def claude(seeded):
    """The seeded Claude Code provider, pointed at the fake CLI."""
    provider = provider_service.get_by_slug(seeded, "claude-code")
    provider.adapter = {**provider.adapter, "config": {**provider.adapter.get("config", {}), "cli": FAKE, "timeout_seconds": 30}}
    seeded.flush()
    return provider


def spec_for(provider) -> ProviderSpec:
    return provider_service.to_spec(provider)


def request_for(workspace: Workspace, prompt: str, permissions=None) -> InvocationRequest:
    return InvocationRequest(
        task_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), request=prompt,
        input={"workspace": workspace_service.to_input(workspace), "permissions": permissions or ["read", "write", "run_commands"]},
    )


# ----------------------------------------------------------------------------- workspaces

def test_workspace_path_validation(tmp_path: Path):
    assert ws_mod.validate_workspace_path(str(tmp_path)) == tmp_path.resolve()
    with pytest.raises(ws_mod.WorkspaceUnavailable):
        ws_mod.validate_workspace_path("relative/path")
    with pytest.raises(ws_mod.WorkspaceUnavailable):
        ws_mod.validate_workspace_path(str(tmp_path / "missing"))
    (tmp_path / "file").write_text("x")
    with pytest.raises(ws_mod.WorkspaceUnavailable):
        ws_mod.validate_workspace_path(str(tmp_path / "file"))
    assert ws_mod.validate_workspace_path("~") == Path.home().resolve()


def test_only_enabled_workspace_ids_resolve(seeded, workspace):
    assert workspace_service.resolve_workspace(seeded, str(workspace.id)) is workspace
    for bad in ["/etc", "../../etc/passwd", "demo", "Demo", str(workspace.path), "", None, 42]:
        assert workspace_service.resolve_workspace(seeded, bad) is None, bad
    workspace.enabled = False
    assert workspace_service.resolve_workspace(seeded, str(workspace.id)) is None


def test_workspace_seed_from_config_is_idempotent(seeded, tmp_path: Path):
    cfg = tmp_path / "workspaces.json"
    cfg.write_text(json.dumps({"workspaces": [{"slug": "one", "name": "One", "path": "~/one", "permissions": ["read", "write", "bogus"]}]}))
    assert workspace_service.seed_from_config(seeded, str(cfg)) == 1
    assert workspace_service.seed_from_config(seeded, str(cfg)) == 0
    row = seeded.query(Workspace).filter_by(slug="one").one()
    assert row.permissions == ["read", "write"]


def test_example_workspace_config_is_valid_and_missing_config_is_fine(seeded, tmp_path: Path):
    example = Path(__file__).resolve().parents[2] / "config" / "workspaces.example.json"
    entries = workspace_service.load_config(str(example))
    assert entries and entries[0]["slug"] == "example-project" and entries[0]["path"].startswith("/path/to")
    assert workspace_service.load_config(str(tmp_path / "does-not-exist.json")) == []
    assert workspace_service.seed_from_config(seeded, str(tmp_path / "does-not-exist.json")) == 0


def test_claude_code_is_registered_as_a_background_provider(seeded):
    provider = provider_service.get_by_slug(seeded, "claude-code")
    assert provider is not None and provider.name == "Claude Code"
    adapter = get_adapter("claude_code")
    assert adapter.execution == "background" and "workspace" in adapter.requires
    from app.schemas.serialise import provider_actions, provider_out

    # Nothing has reported that it can run: no Ask, and a calm note.
    assert provider_actions(provider) == []
    out = provider_out(provider).model_dump()
    assert out["availability"] == {"state": "unavailable", "note": "Not available on this installation"}
    assert "adapter" not in out
    mark_available(seeded)
    assert provider_actions(provider) == ["ask"]
    assert provider_out(provider).availability["note"] is None


def test_availability_states_and_staleness(seeded):
    provider = provider_service.get_by_slug(seeded, "claude-code")
    for state, note in [("not_installed", "Not available on this installation"), ("not_authenticated", "Not signed in on this installation")]:
        provider_service.record_availability(seeded, provider, HealthResult(ok=False, state=state, detail="/usr/bin/whatever"))
        assert provider_service.availability_of(provider) == {"state": state, "note": note}
    mark_available(seeded)
    provider.availability = {**provider.availability, "checked_at": (utcnow() - timedelta(minutes=10)).isoformat()}
    assert provider_service.availability_of(provider)["state"] == "unavailable"  # the worker went away


def test_coding_request_without_a_worker_fails_clearly(seeded):
    with pytest.raises(task_service.NoProviderAvailable, match="Coding help isn't set up on this installation yet."):
        task_service.submit(seeded, "Fix the bug in the login code")
    # non-coding requests are unaffected
    assert task_service.submit(seeded, "Compare two things for me").runs[0].provider.slug == "research"


def test_claude_health_check_without_cli_is_not_installed(claude):
    claude.adapter = {**claude.adapter, "config": {"cli": "/nonexistent/claude"}}
    result = get_adapter("claude_code").check(spec_for(claude), {})
    assert result.ok is False and result.state == "not_installed"


# ----------------------------------------------------------------------------- submit / needs_input

def test_coding_request_routes_to_claude_and_asks_for_a_project(seeded, workspace):
    task = task_service.submit(seeded, "Fix the spacing issue on the Recent page and run the frontend tests")
    run = task.runs[0]
    assert run.provider.slug == "claude-code"
    assert run.execution == "background"
    assert task.state == TaskState.NEEDS_INPUT
    assert run.state == RunState.NEEDS_INPUT
    assert run.input_request["question"] == "Which project should I work on?"
    assert [o["label"] for o in run.input_request["options"]] == ["Demo"]
    assert "workspace_id" not in run.input


def test_named_project_is_inferred(seeded, workspace):
    task = task_service.submit(seeded, "Add a test to the Demo project for the hello function")
    assert task.state == TaskState.QUEUED
    assert task.runs[0].input["workspace_id"] == str(workspace.id)
    assert task.runs[0].input["permissions"] == ["read", "write", "run_commands"]


def test_no_workspaces_means_an_honest_failure(seeded):
    mark_available(seeded)
    task = task_service.submit(seeded, "Fix the bug in the login code")
    assert task.state == TaskState.FAILED
    assert task.summary == "No project is set up for this kind of work yet."


def test_answering_the_project_question(seeded, workspace):
    task = task_service.submit(seeded, "Refactor the config module")
    with pytest.raises(task_service.InvalidInput):
        task_service.answer_input(seeded, task, "/etc")
    with pytest.raises(task_service.InvalidInput):
        task_service.answer_input(seeded, task, str(uuid.uuid4()))
    run = task_service.answer_input(seeded, task, str(workspace.id))
    assert task.state == TaskState.QUEUED and run.state == RunState.PENDING
    assert run.input_request is None
    assert run.input["workspace_id"] == str(workspace.id)
    with pytest.raises(task_service.InvalidInput):
        task_service.answer_input(seeded, task, str(workspace.id))


def test_submit_never_accepts_a_path_as_workspace(seeded, workspace):
    claude = provider_service.get_by_slug(seeded, "claude-code")
    with pytest.raises(task_service.InvalidInput):
        task_service.submit(seeded, "do it", provider=claude, input={"workspace_id": str(workspace.path)})
    task = task_service.submit(seeded, "do it", provider=claude, input={"workspace_id": str(workspace.id), "path": "/etc"})
    assert "path" not in task.runs[0].input


# ----------------------------------------------------------------------------- adapter lifecycle

def test_command_translates_permissions_and_blocks_dangerous_commands():
    cmd = build_command("claude", "hello", ["read"], {})
    assert "--restricted" in cmd and "none" == cmd[cmd.index("--permission-prompts") + 1]
    assert "Bash" not in cmd[cmd.index("--tools") + 1]
    assert "Edit" in cmd  # disallowed explicitly
    cmd = build_command("claude", "hello", ["read", "write", "run_commands"], {"max_turns": 5})
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--allowedTools") + 1] == "Bash"
    assert any(x.startswith("Bash(git push") for x in cmd) and any(x.startswith("Bash(sudo") for x in cmd)
    assert cmd[cmd.index("--max-turns") + 1] == "5"
    assert "--dangerously-skip-permissions" not in cmd


def test_full_run_completes_with_summary_changes_and_progress(claude, workspace, repo, tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_CLAUDE_ARGV_FILE", str(argv_file))
    steps: list[str] = []
    context = InvocationContext(progress=steps.append, log_dir=str(tmp_path / "logs"))
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(workspace, "Add a hello module"), context)

    assert result.state == ResultState.COMPLETED
    assert result.summary == "Added a hello module and the tests pass."
    assert result.external_ref == "fake-session-1"
    assert "Inspecting the project" in steps and "Making changes" in steps and "Running checks" in steps
    types = [a.type for a in result.artifacts]
    assert types == ["report", "structured", "diff"]
    changed = result.artifacts[1]
    assert changed.payload["rows"] == [["new_module.py", "Created"], ["app.py", "Modified"]]
    assert changed.metadata["pre_existing"] == ["dirty.txt"]
    assert "changed by fake claude" in result.artifacts[2].payload["text"]
    assert result.metadata["git_before"]["dirty_files"] == ["dirty.txt"]
    assert result.metadata["cost_usd"] == 0.12 and result.metadata["exit_code"] == 0
    # the raw stream went to the server-side log, not into the result
    log = next((tmp_path / "logs").glob("*.jsonl")).read_text()
    assert '"type": "system"' in log and "tool_use" in log
    argv = json.loads(argv_file.read_text())
    assert "--restricted" in argv and "--no-session-persistence" in argv
    assert (repo / "dirty.txt").read_text().endswith("user edit before the task\n")


def test_pre_existing_dirty_file_is_attributed_only_when_touched(claude, workspace, repo):
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(workspace, "TOUCH_DIRTY please"))
    changed = next(a for a in result.artifacts if a.type == "structured")
    assert changed.payload["rows"] == [["new_module.py", "Created"], ["app.py", "Modified"], ["dirty.txt", "Modified"]]
    assert changed.metadata["pre_existing"] == []


def test_run_without_git_uses_snapshots(claude, tmp_path, seeded):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "app.py").write_text("x = 1\n")
    ws = Workspace(slug="plain", name="Plain", path=str(plain), permissions=["read", "write"])
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(ws, "change things"))
    assert result.state == ResultState.COMPLETED
    changed = next(a for a in result.artifacts if a.type == "structured")
    assert changed.payload["rows"] == [["new_module.py", "Created"], ["app.py", "Modified"]]
    assert all(a.type != "diff" for a in result.artifacts)


def test_no_changes_summary(claude, workspace):
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(workspace, "NO_CHANGES just look"))
    assert result.state == ResultState.COMPLETED
    assert [a.type for a in result.artifacts] == ["report"]


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        ("CRASH now", "Claude Code could not complete this request."),
        ("ERROR_RESULT now", "Claude Code could not complete this request."),
        ("MAX_TURNS now", "Claude Code stopped before finishing: it reached its step limit."),
    ],
)
def test_cli_failures_are_human_readable(claude, workspace, prompt, message):
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(workspace, prompt))
    assert result.state == ResultState.FAILED
    assert result.error == message
    assert "Traceback" not in (result.error or "") and "\x1b" not in (result.error or "")


def test_missing_cli_and_missing_workspace(claude, workspace, tmp_path):
    claude.adapter = {**claude.adapter, "config": {"cli": "/nonexistent/claude"}}
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(workspace, "x"))
    assert result.state == ResultState.FAILED and result.error == "Claude Code could not start."
    claude.adapter = {**claude.adapter, "config": {"cli": FAKE}}
    gone = Workspace(slug="gone", name="Gone", path=str(tmp_path / "nope"), permissions=["read"])
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(gone, "x"))
    assert result.state == ResultState.FAILED and result.error == "The workspace is unavailable."


def test_output_is_sanitised(claude, workspace):
    result = get_adapter("claude_code").invoke(spec_for(claude), request_for(workspace, "NOISY output"))
    assert result.summary == "All good."
    report = result.artifacts[0].payload["text"]
    assert "\x1b" not in report and "\x07" not in report and "Bold output with noise." in report
    assert "Summary:" not in report  # the summary line becomes the task summary, not part of the report
    assert strip_noise("a" * 30_000, limit=100).endswith("[shortened]")


def test_cancellation_kills_the_process(claude, workspace):
    adapter = get_adapter("claude_code")
    flag = threading.Event()
    context = InvocationContext(cancelled=flag.is_set)
    request = request_for(workspace, "SLOW task")
    holder: dict[str, object] = {}

    def run() -> None:
        holder["result"] = adapter.invoke(spec_for(claude), request, context)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 10
    while request.run_id not in adapter._procs and time.monotonic() < deadline:
        time.sleep(0.05)
    proc = adapter._procs[request.run_id]
    assert proc.poll() is None
    flag.set()
    thread.join(timeout=15)
    assert not thread.is_alive()
    result = holder["result"]
    assert result.state == ResultState.CANCELLED
    assert result.error == "The task was stopped before completion."
    assert proc.poll() is not None
    assert request.run_id not in adapter._procs


def test_cancel_via_adapter_api(claude, workspace):
    adapter = get_adapter("claude_code")
    request = request_for(workspace, "SLOW task")
    holder: dict[str, object] = {}
    thread = threading.Thread(target=lambda: holder.__setitem__("r", adapter.invoke(spec_for(claude), request)))
    thread.start()
    while request.run_id not in adapter._procs:
        time.sleep(0.05)
    assert adapter.cancel(spec_for(claude), request.run_id, {}) is True
    thread.join(timeout=15)
    assert holder["r"].state in {ResultState.FAILED, ResultState.CANCELLED}
    assert adapter.cancel(spec_for(claude), request.run_id, {}) is False


# ----------------------------------------------------------------------------- service + worker

def test_background_run_lifecycle_through_the_service(seeded, claude, workspace):
    task = task_service.submit(seeded, "Refactor the code in the Demo project")
    run = task.runs[0]
    assert run.execution == "background" and run.state == RunState.PENDING
    task_service.begin_run(seeded, run, worker_id="w1")
    assert task.state == TaskState.WORKING and run.progress["phase"] == "Working…"

    request = task_service.build_request(seeded, run)
    assert request.input["workspace"]["path"] == str(workspace.path)
    steps: list[str] = []
    result = task_service.invoke_adapter(claude, request, InvocationContext(progress=steps.append))
    task_service.finish_run(seeded, run, result)
    seeded.refresh(task)
    assert task.state == TaskState.COMPLETED
    assert task.summary == "Added a hello module and the tests pass."
    assert {a.type for a in task.artifacts} == {"report", "structured", "diff"}
    assert run.meta["session_id"] == "fake-session-1"
    assert run.progress["phase"] is None


def test_cancelling_a_running_background_task(seeded, claude, workspace):
    task = task_service.submit(seeded, "Refactor the code in the Demo project")
    run = task.runs[0]
    task_service.begin_run(seeded, run, worker_id="w1")
    task_service.cancel_task(seeded, task)
    assert task.state == TaskState.CANCELLED
    assert run.state == RunState.RUNNING and run.cancel_requested is True
    # the worker then stops the process and records the outcome
    from adapters import InvocationResult

    task_service.finish_run(seeded, run, InvocationResult(state=ResultState.COMPLETED, summary="finished anyway"))
    assert run.state == RunState.CANCELLED
    assert task.state == TaskState.CANCELLED and task.summary == "Cancelled."


def test_cancelling_a_task_waiting_for_input(seeded, workspace):
    task = task_service.submit(seeded, "Fix the flaky test")
    task_service.cancel_task(seeded, task)
    assert task.state == TaskState.CANCELLED and task.runs[0].state == RunState.CANCELLED
    assert task.runs[0].input_request is None


def test_stale_worker_runs_are_failed(seeded, claude, workspace):
    task = task_service.submit(seeded, "Refactor the code in the Demo project")
    run = task.runs[0]
    task_service.begin_run(seeded, run, worker_id="dead")
    run.heartbeat_at = utcnow() - timedelta(minutes=10)
    seeded.flush()
    assert task_service.reap_stale_runs(seeded) == 1
    seeded.refresh(task)
    assert task.state == TaskState.FAILED and task.summary == "The task was stopped before completion."


def test_worker_acquires_pending_background_runs_once(seeded, claude, workspace):
    task = task_service.submit(seeded, "Refactor the code in the Demo project")
    waiting = task_service.submit(seeded, "Fix the flaky test")  # needs input: must not be picked up
    seeded.flush()
    run_id = acquire(seeded, ["claude_code"], "w1")
    assert run_id == task.runs[0].id
    seeded.refresh(task)
    assert task.state == TaskState.WORKING and task.runs[0].worker_id == "w1"
    assert acquire(seeded, ["claude_code"], "w1") is None
    assert waiting.state == TaskState.NEEDS_INPUT
    # once answered, the second task becomes acquirable (input_request must be a real SQL NULL)
    task_service.answer_input(seeded, waiting, str(workspace.id))
    seeded.flush()
    assert acquire(seeded, ["claude_code"], "w1") == waiting.runs[0].id
