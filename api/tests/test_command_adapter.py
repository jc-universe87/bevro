"""The command adapter: a program per task, no shell, inside approved folders."""

import sys
import threading
import time
from pathlib import Path

import pytest

from adapters import InvocationContext, InvocationRequest, ProviderSpec, get_adapter
from adapters.command import CommandUnavailable, build_argv, mentioned_files, resolve_cwd, summary_line
from tests.connect_fixtures import make_python_project


def run(spec: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None):
    """invoke() then collect_artifacts(): what the runtime layer does for every run."""
    adapter = get_adapter("command")
    result = adapter.invoke(spec, request, context)
    return result.model_copy(update={"artifacts": adapter.collect_artifacts(spec, result, context)})


def spec_for(project: Path, **overrides) -> ProviderSpec:
    config = {
        "argv": [str(project / ".venv" / "bin" / "python"), "-m", "fixture_research.agent"],
        "cwd": str(project),
        "input": {"mode": "flag", "flag": "--topic"},
        "output": {"mode": "stdout"},
        "env": {"PYTHONPATH": str(project / "src")},
        "secret_env": ["OPENAI_API_KEY"],
        "timeout_seconds": 60,
        **overrides,
    }
    return ProviderSpec(id="1", slug="fixture", name="Fixture Research", adapter={"kind": "command", "config": config})


def test_build_argv_modes_never_interpolate(tmp_path):
    cwd = tmp_path
    argv, stdin, env = build_argv({"argv": [sys.executable, "-m", "x"], "input": {"mode": "flag", "flag": "--topic"}}, 'a; rm -rf / "quoted"', cwd)
    assert argv[1:] == ["-m", "x", "--topic", 'a; rm -rf / "quoted"'] and stdin is None and env == {}
    argv, stdin, _ = build_argv({"argv": [sys.executable], "input": {"mode": "stdin"}}, "hello", cwd)
    assert argv == [sys.executable] and stdin == "hello\n"
    argv, _, _ = build_argv({"argv": [sys.executable], "input": {"mode": "argument"}}, "hi", cwd)
    assert argv[-1] == "hi"
    argv, _, env = build_argv({"argv": [sys.executable], "input": {"mode": "environment", "name": "TASK"}}, "hi", cwd)
    assert argv == [sys.executable] and env == {"TASK": "hi"}
    scratch = tmp_path / "scratch"
    argv, _, _ = build_argv({"argv": [sys.executable], "input": {"mode": "file", "flag": "--input"}}, "from a file", cwd, scratch)
    assert argv[-2] == "--input" and Path(argv[-1]).read_text() == "from a file" and Path(argv[-1]).parent == scratch
    with pytest.raises(CommandUnavailable):
        build_argv({"argv": ["definitely-not-a-program-xyz"]}, "hi", cwd)
    with pytest.raises(CommandUnavailable):
        build_argv({"argv": [sys.executable], "input": {"mode": "flag", "flag": "topic"}}, "hi", cwd)


def test_working_directory_must_be_inside_the_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(tmp_path / "agents"))
    (tmp_path / "agents" / "p").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    assert resolve_cwd({"cwd": str(tmp_path / "agents" / "p")}) == (tmp_path / "agents" / "p").resolve()
    with pytest.raises(CommandUnavailable):
        resolve_cwd({"cwd": str(tmp_path / "elsewhere")})
    monkeypatch.delenv("BEVRO_LOCAL_ROOTS")
    with pytest.raises(CommandUnavailable):
        resolve_cwd({"cwd": str(tmp_path / "agents" / "p")})  # no roots: nothing runs anywhere
    # No cwd: Bevro's own integration folder is created and used.
    integration = tmp_path / "integrations" / "p1"
    assert resolve_cwd({}, {"integration_dir": str(integration)}) == integration.resolve() and integration.is_dir()


def test_mentioned_files_stay_inside_the_folder(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "a.md").write_text("x", encoding="utf-8")
    outside = tmp_path.parent / "outside.md"
    outside.write_text("y", encoding="utf-8")
    text = f"Report written to reports/a.md and {outside} and reports/missing.md"
    assert mentioned_files(text, tmp_path) == [(tmp_path / "reports" / "a.md").resolve()]


def test_summary_is_the_last_line_shortened():
    assert summary_line("Working...\nReport written to reports/x.md\n", "X") == "Report written to reports/x.md."
    assert summary_line("", "X") == "Done. X finished without printing anything."


def test_runs_the_fixture_agent_and_attaches_its_report(monkeypatch, tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    adapter = get_adapter("command")
    assert adapter.check(spec_for(project), {}).ok
    phases: list[str] = []
    ctx = InvocationContext(progress=phases.append, log_dir=str(tmp_path / "logs"))
    result = run(spec_for(project), InvocationRequest(task_id="t", run_id="r1", request="What changed in widgets?", secrets={"OPENAI_API_KEY": "sk-test"}), ctx)
    assert result.state == "completed", result
    assert result.summary == "Report written to reports/latest-findings.md."
    assert [a.title for a in result.artifacts] == ["Output from Fixture Research", "latest-findings.md"]
    assert "Topic: What changed in widgets?" in result.artifacts[1].payload["text"]
    assert result.metadata["files"] == ["reports/latest-findings.md"] and result.metadata["exit_code"] == 0
    assert phases == ["Running Fixture Research", "Collecting the result"]
    assert (tmp_path / "logs" / "r1.log").is_file()  # raw output stays server-side
    assert "output" not in result.metadata  # and never in the run record


def test_report_directory_output_attaches_new_files_only(monkeypatch, tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    (project / "reports").mkdir()
    (project / "reports" / "old.md").write_text("old", encoding="utf-8")
    (project / "src" / "fixture_research" / "agent.py").write_text(
        "from pathlib import Path\nPath('reports/new.md').write_text('fresh')\nprint('done, quietly')\n", encoding="utf-8"
    )
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    spec = spec_for(project, output={"modes": ["stdout", "report_dir"], "report_dir": "reports"}, input={"mode": "none"}, secret_env=[])
    result = run(spec, InvocationRequest(task_id="t", run_id="r8", request="q"))
    assert result.state == "completed"
    assert [a.title for a in result.artifacts] == ["Output from Fixture Research", "new.md"]
    assert result.metadata["files"] == ["reports/new.md"]


def test_secret_is_passed_as_environment_only(monkeypatch, tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    (project / "src" / "fixture_research" / "agent.py").write_text("import os, sys\nprint('key' if os.environ.get('OPENAI_API_KEY') == 'sk-test' else 'nokey')\nprint(sys.argv[1:])\n", encoding="utf-8")
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    result = run(spec_for(project), InvocationRequest(task_id="t", run_id="r2", request="q", secrets={"OPENAI_API_KEY": "sk-test"}))
    text = result.artifacts[0].payload["text"]
    assert text.startswith("key") and "sk-test" not in text  # in the environment, not on the command line


def test_failure_and_refusals_are_honest(monkeypatch, tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    (project / "src" / "fixture_research" / "agent.py").write_text("import sys\nprint('boom')\nsys.exit(3)\n", encoding="utf-8")
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    key = {"OPENAI_API_KEY": "sk-test"}
    result = run(spec_for(project), InvocationRequest(task_id="t", run_id="r3", request="q", secrets=key))
    assert result.state == "failed" and result.error == "Fixture Research started but couldn't finish this task."
    assert result.failure == "invocation_failed" and result.metadata["exit_code"] == 3
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(tmp_path / "other"))
    (tmp_path / "other").mkdir()
    result = get_adapter("command").invoke(spec_for(project), InvocationRequest(task_id="t", run_id="r4", request="q", secrets=key))
    assert result.state == "failed" and "could not start" in result.error and result.failure == "configuration_problem"
    assert "outside the approved" in result.metadata["detail"]


def test_missing_credential_is_refused_before_anything_runs(monkeypatch, tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = get_adapter("command").invoke(spec_for(project), InvocationRequest(task_id="t", run_id="r6", request="q"))
    assert result.state == "failed" and result.failure == "credential_required"
    assert result.error == "Fixture Research needs a credential before it can run."
    assert result.metadata == {"missing_secrets": ["OPENAI_API_KEY"]}
    assert not (project / "reports").exists()  # the program was never started


def test_a_credential_the_program_rejects_is_classified(monkeypatch, tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    (project / "src" / "fixture_research" / "agent.py").write_text(
        "import sys\nraise SystemExit('openai.OpenAIError: The api_key client option must be set either by passing api_key or by setting the OPENAI_API_KEY environment variable')\n", encoding="utf-8"
    )
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    result = get_adapter("command").invoke(spec_for(project), InvocationRequest(task_id="t", run_id="r7", request="q", secrets={"OPENAI_API_KEY": "sk-wrong"}))
    assert result.state == "failed" and result.failure == "credential_required"
    assert result.error == "Fixture Research couldn't use its credential."
    assert "sk-wrong" not in str(result.model_dump())


def test_cancellation_stops_the_process(monkeypatch, tmp_path):
    root = tmp_path / "agents"
    project = make_python_project(root)
    (project / "src" / "fixture_research" / "agent.py").write_text("import time\nprint('started', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(root))
    stop = threading.Event()
    threading.Timer(0.8, stop.set).start()
    started = time.monotonic()
    result = get_adapter("command").invoke(spec_for(project), InvocationRequest(task_id="t", run_id="r5", request="q", secrets={"OPENAI_API_KEY": "sk-test"}), InvocationContext(cancelled=stop.is_set))
    assert result.state == "cancelled" and result.failure == "cancelled" and time.monotonic() - started < 15
