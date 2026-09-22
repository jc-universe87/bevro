"""Claude Code adapter: runs the local `claude` CLI non-interactively.

Bevro -> Task -> ProviderRun -> this adapter -> `claude -p ...` -> workspace

Verified against Claude Code 2.1.278 on this machine:
  -p / --print, --output-format stream-json, --verbose, --permission-mode,
  --permission-prompts none, --allowedTools, --disallowedTools, --tools,
  --restricted, --no-session-persistence, --max-turns, --max-budget-usd,
  --append-system-prompt.

The CLI uses the machine's existing Claude Code login; nothing is copied.
Raw stream output goes to a server-side log file only. What reaches Bevro is
a short phase ("Running checks"), the final result text, and what changed in
the workspace.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from adapters import workspace as ws
from adapters.base import (
    ArtifactDraft,
    FailureKind,
    HealthResult,
    InvocationContext,
    InvocationRequest,
    InvocationResult,
    NotSupported,
    ProviderSpec,
    ResultState,
)
from adapters.registry import register_adapter
from adapters.runtime import BaseRuntimeAdapter

DEFAULT_MAX_TURNS = 40
DEFAULT_MAX_BUDGET_USD = 2.0
DEFAULT_TIMEOUT_S = 1800
CANCEL_GRACE_S = 5.0

# Tool families and the phase wording a user sees for them.
_INSPECT = {"Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "TodoRead"}
_CHANGE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_CHECK_RE = re.compile(
    r"\b(pytest|unittest|vitest|jest|npm (test|run (test|build|lint|typecheck))|npx (vitest|jest|tsc|eslint)|tsc|make\b|"
    r"cargo (test|build)|go (test|build)|ruff|eslint|mypy|flake8|lint|docker compose (exec|run) .*(pytest|test))\b"
)
_GIT_RE = re.compile(r"^\s*git\b")
_LOOK_RE = re.compile(r"^\s*(ls|cat|head|tail|find|grep|rg|tree|wc|pwd|which|stat|file|less|sed -n)\b")

# Commands that are never allowed, whatever the granted permissions.
BLOCKED_COMMANDS = [
    "Bash(git push*)", "Bash(git push:*)",
    "Bash(sudo*)", "Bash(sudo:*)", "Bash(su *)", "Bash(doas*)",
    "Bash(rm -rf /*)", "Bash(rm -rf ~*)", "Bash(rm -rf .*)",
    "Bash(git reset --hard*)", "Bash(git clean*)", "Bash(git checkout -- .*)",
    "Bash(dropdb*)", "Bash(psql*DROP*)", "Bash(docker compose down -v*)",
    "Bash(chmod -R*)", "Bash(chown*)", "Bash(mkfs*)", "Bash(dd *)",
]

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SUMMARY_RE = re.compile(r"^\s*\**summary\**\s*[:\-–]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

SYSTEM_PROMPT = (
    "You are working for a person through Bevro, a workspace that hands tasks to agents. "
    "Write only inside the current working directory. Any other directory you have been given is "
    "reference material: read it, never change it, never create files in it. Do not run git commit, "
    "git push, or anything destructive. Do not ask questions; make reasonable decisions and note them. "
    "When you are done, end your final message with one line that starts with 'Summary:' - a single "
    "plain-English sentence for a non-technical reader saying what changed and whether the project "
    "checks passed."
)


def strip_noise(text: str, limit: int = 20_000) -> str:
    """Remove terminal escape codes and control characters; cap the length."""
    cleaned = _CTRL_RE.sub("", _ANSI_RE.sub("", text or ""))
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "\n\n[shortened]"
    return cleaned.strip()


def looks_signed_in() -> bool:
    """A best-effort check for a Claude Code login on this machine."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    return (config_dir / ".credentials.json").is_file()


def clean_env() -> dict[str, str]:
    """The CLI must not think it is nested inside another Claude Code session."""
    env = {k: v for k, v in os.environ.items() if not (k == "CLAUDECODE" or k.startswith("CLAUDE_"))}
    env.setdefault("TERM", "dumb")
    return env


def phase_for_tool(name: str, tool_input: dict[str, Any] | None) -> str | None:
    if name in _CHANGE:
        return "Making changes"
    if name in _INSPECT:
        return "Inspecting the project"
    if name == "Bash":
        command = str((tool_input or {}).get("command", ""))
        if _CHECK_RE.search(command):
            return "Running checks"
        if _GIT_RE.match(command):
            return "Checking the repository"
        if _LOOK_RE.match(command):
            return "Inspecting the project"
        return "Running commands"
    return None


def build_command(cli: str, prompt: str, permissions: list[str], config: dict[str, Any], read_paths: list[str] | None = None) -> list[str]:
    """Translate Bevro permissions into CLI flags. Read is implied.

    `read_paths` are directories the run may look at but must not change (the
    project an integration bridge is being built against). They are added to
    the session and named in the prompt; what actually protects them is that
    the run's own directory is elsewhere, and that Bevro compares the folder
    before and after and refuses the result if anything moved.
    """
    tools = ["Read", "Glob", "Grep"]
    allowed: list[str] = []
    disallowed = list(BLOCKED_COMMANDS)
    if "write" in permissions:
        tools += ["Edit", "Write", "MultiEdit", "NotebookEdit"]
    else:
        disallowed += ["Edit", "Write", "MultiEdit", "NotebookEdit"]
    if "run_commands" in permissions:
        tools.append("Bash")
        allowed.append("Bash")
    cmd = [
        cli, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--no-session-persistence",
        "--restricted",                       # confines file tools to the working directory, ignores personal settings
        "--tools", ",".join(tools),
        "--permission-prompts", "none",       # nothing can wait on a human; anything that would ask is denied
        "--permission-mode", "acceptEdits" if "write" in permissions else "default",
        "--max-turns", str(int(config.get("max_turns", DEFAULT_MAX_TURNS))),
        "--max-budget-usd", str(float(config.get("max_budget_usd", DEFAULT_MAX_BUDGET_USD))),
        "--append-system-prompt", SYSTEM_PROMPT,
    ]
    if allowed:
        cmd += ["--allowedTools", *allowed]
    cmd += ["--disallowedTools", *disallowed]
    for directory in read_paths or []:
        cmd += ["--add-dir", str(directory)]
    if config.get("model"):
        cmd += ["--model", str(config["model"])]
    return cmd


def strip_summary_line(result_text: str) -> str:
    """The report keeps everything except the trailing 'Summary:' line, which becomes the task summary."""
    matches = list(_SUMMARY_RE.finditer(result_text or ""))
    if not matches:
        return result_text
    last = matches[-1]
    if last.end() >= len(result_text.rstrip()) - 1:
        return result_text[: last.start()].rstrip()
    return result_text


def parse_summary(result_text: str, changes: ws.Changes) -> str:
    match = None
    for match in _SUMMARY_RE.finditer(result_text or ""):
        pass
    if match:
        line = strip_noise(match.group(1), 300)
        if line:
            return line if line.endswith((".", "!", "?")) else line + "."
    if changes.count == 0:
        return "Done. No files were changed."
    return f"Done. {changes.count} file{'s' if changes.count != 1 else ''} changed."


def friendly_error(subtype: str | None, result_text: str | None) -> str:
    if subtype == "error_max_turns":
        return "Claude Code stopped before finishing: it reached its step limit."
    if subtype and "budget" in subtype:
        return "Claude Code stopped before finishing: it reached its spending limit."
    return "Claude Code could not complete this request."


class ClaudeCodeAdapter(BaseRuntimeAdapter):
    kind = "claude_code"
    execution = "background"
    requires = frozenset({"workspace"})

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    # -- health -------------------------------------------------------------
    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult:
        """Is the CLI here, does it run, and does it look signed in?

        Sign-in is a heuristic (a credentials file or an API key in the
        environment); the CLI has no cheap "am I logged in" command.
        """
        cli = self._cli(provider)
        if shutil.which(cli) is None:
            return HealthResult(ok=False, state="not_installed", detail="Claude Code CLI not found")
        try:
            out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20, env=clean_env(), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return HealthResult(ok=False, state="unavailable", detail=str(exc))
        if out.returncode != 0:
            return HealthResult(ok=False, state="unavailable", detail=strip_noise(out.stderr, 120) or "CLI failed to start")
        if not looks_signed_in():
            return HealthResult(ok=False, state="not_authenticated", detail="no Claude Code login found")
        return HealthResult(ok=True, state="available", detail=strip_noise(out.stdout, 80) or None)

    def _cli(self, provider: ProviderSpec) -> str:
        return str(provider.adapter_config.get("cli") or "claude")

    # -- invoke ---------------------------------------------------------------
    def invoke(
        self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None
    ) -> InvocationResult:
        context = context or InvocationContext()
        config = provider.adapter_config
        workspace = request.input.get("workspace") or {}
        try:
            path = ws.validate_workspace_path(str(workspace.get("path", "")))
        except ws.WorkspaceUnavailable as exc:
            return self._log_and_fail(context, request, f"workspace: {exc}", "The workspace is unavailable.")
        permissions = [p for p in request.input.get("permissions", workspace.get("permissions", ["read"])) if isinstance(p, str)]
        cli = self._cli(provider)
        if shutil.which(cli) is None:
            return self._log_and_fail(context, request, f"cli not found: {cli}", "Claude Code could not start.")

        excludes = [Path(p) for p in request.input.get("exclude_paths", []) if isinstance(p, str)]
        use_git = ws.is_git_repo(path)
        git_before = ws.git_state(path) if use_git else None
        snap_before = None if use_git else ws.snapshot(path, excludes)
        meta: dict[str, Any] = {"workspace_id": workspace.get("id"), "permissions": permissions, "git_before": git_before.to_meta() if git_before else None}

        context.progress("Starting Claude Code")
        read_paths = [str(p) for p in (workspace.get("read_paths") or []) if isinstance(p, str)]
        cmd = build_command(cli, request.request, permissions, config, read_paths)
        log_path = self._log_path(context, request)
        stderr_path = log_path.with_suffix(".stderr") if log_path else None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=path,
                stdout=subprocess.PIPE,
                stderr=stderr_path.open("w") if stderr_path else subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                env=clean_env(),
                start_new_session=True,  # own process group, so cancel can take the whole tree down
            )
        except OSError as exc:
            return self._log_and_fail(context, request, f"could not start: {exc}", "Claude Code could not start.")
        with self._lock:
            self._procs[request.run_id] = proc

        result_event: dict[str, Any] | None = None
        session_id: str | None = None
        last_phase: str | None = None
        cancelled = False
        deadline = time.monotonic() + float(config.get("timeout_seconds", DEFAULT_TIMEOUT_S))
        log = log_path.open("a") if log_path else None
        try:
            assert proc.stdout is not None
            for line in self._lines(proc, context, deadline):
                if line is None:  # cancelled or timed out
                    cancelled = True
                    break
                if log:
                    log.write(line)
                    if not line.endswith("\n"):
                        log.write("\n")
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                etype = event.get("type")
                if etype == "system" and event.get("subtype") == "init":
                    session_id = event.get("session_id")
                elif etype == "assistant":
                    for block in (event.get("message") or {}).get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            phase = phase_for_tool(str(block.get("name")), block.get("input"))
                            if phase and phase != last_phase:
                                last_phase = phase
                                context.progress(phase)
                elif etype == "result":
                    result_event = event
            proc.wait(timeout=30)
        finally:
            if log:
                log.close()
            with self._lock:
                self._procs.pop(request.run_id, None)
            if proc.poll() is None:
                self._terminate(proc)

        meta["session_id"] = session_id
        meta["exit_code"] = proc.returncode
        if result_event:
            meta["cost_usd"] = result_event.get("total_cost_usd")
            meta["duration_ms"] = result_event.get("duration_ms")
            meta["num_turns"] = result_event.get("num_turns")
            meta["permission_denials"] = len(result_event.get("permission_denials") or [])

        context.progress("Reviewing what changed")
        if use_git and git_before is not None:
            git_after = ws.git_state(path)
            changes = ws.changes_from_git(git_before, git_after)
            meta["git_after"] = git_after.to_meta()
        else:
            changes = ws.changes_from_snapshots(snap_before or {}, ws.snapshot(path, excludes))
        meta["changes"] = {"created": changes.created, "modified": changes.modified, "deleted": changes.deleted, "pre_existing": changes.pre_existing}

        if cancelled:
            return self._with_meta(
                InvocationResult(
                    state=ResultState.CANCELLED,
                    error="The task was stopped before completion.",
                    artifacts=self._change_artifacts(path, workspace, changes, use_git),
                    external_ref=session_id,
                ),
                meta,
            )

        artifacts: list[ArtifactDraft] = []
        result_text = strip_noise(str(result_event.get("result") or "")) if result_event else ""
        if result_event is None or proc.returncode not in (0, None):
            return self._with_meta(
                InvocationResult(state=ResultState.FAILED, error="Claude Code could not complete this request.", failure=FailureKind.INVOCATION_FAILED, artifacts=self._change_artifacts(path, workspace, changes, use_git)),
                meta,
            )
        if result_event.get("is_error") or str(result_event.get("subtype", "")).startswith("error"):
            error = friendly_error(result_event.get("subtype"), result_text)
            if result_text:
                artifacts.append(ArtifactDraft(type="report", title="What Claude Code reported", mime_type="text/markdown", payload={"text": result_text}))
            artifacts += self._change_artifacts(path, workspace, changes, use_git)
            return self._with_meta(InvocationResult(state=ResultState.FAILED, error=error, failure=FailureKind.INVOCATION_FAILED, artifacts=artifacts), meta)

        summary = parse_summary(result_text, changes)
        report = strip_summary_line(result_text)
        if report:
            artifacts.append(ArtifactDraft(type="report", title="What Claude Code did", summary=None, mime_type="text/markdown", payload={"text": report}))
        artifacts += self._change_artifacts(path, workspace, changes, use_git)
        return self._with_meta(InvocationResult(state=ResultState.COMPLETED, summary=summary, artifacts=artifacts, external_ref=session_id), meta)

    # -- status / cancel ------------------------------------------------------
    def get_status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        with self._lock:
            proc = self._procs.get(external_ref)
        if proc is None:
            raise NotSupported("no running process for this run in this worker")
        return InvocationResult(state=ResultState.RUNNING if proc.poll() is None else ResultState.COMPLETED, external_ref=external_ref)

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool:
        """external_ref is the run id while the process lives in this worker."""
        with self._lock:
            proc = self._procs.get(external_ref)
        if proc is None:
            return False
        self._terminate(proc)
        return True

    # -- helpers ------------------------------------------------------------
    def _lines(self, proc: subprocess.Popen[str], context: InvocationContext, deadline: float):
        """Yield stdout lines; yield None once if cancelled or out of time."""
        assert proc.stdout is not None
        queue: list[str | None] = []
        lock = threading.Lock()

        def pump() -> None:
            for raw in proc.stdout:  # type: ignore[union-attr]
                with lock:
                    queue.append(raw)
            with lock:
                queue.append(None)

        threading.Thread(target=pump, daemon=True).start()
        while True:
            with lock:
                batch, queue[:] = queue[:], []
            for item in batch:
                if item is None:
                    return
                yield item
            if context.cancelled() or time.monotonic() > deadline:
                self._terminate(proc)
                yield None
                return
            time.sleep(0.2)

    @staticmethod
    def _terminate(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            proc.terminate()
        try:
            proc.wait(timeout=CANCEL_GRACE_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait(timeout=5)

    @staticmethod
    def _log_path(context: InvocationContext, request: InvocationRequest) -> Path | None:
        if not context.log_dir:
            return None
        directory = Path(context.log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{request.run_id}.jsonl"

    def _log_and_fail(self, context: InvocationContext, request: InvocationRequest, technical: str, friendly: str) -> InvocationResult:
        path = self._log_path(context, request)
        if path:
            with path.open("a") as fh:
                fh.write(json.dumps({"type": "bevro_error", "detail": technical}) + "\n")
        failure = FailureKind.CONFIGURATION_PROBLEM if "workspace" in technical or "cli not found" in technical else FailureKind.INVOCATION_FAILED
        return InvocationResult(state=ResultState.FAILED, error=friendly, failure=failure)

    @staticmethod
    def _with_meta(result: InvocationResult, meta: dict[str, Any]) -> InvocationResult:
        return result.model_copy(update={"metadata": meta})

    @staticmethod
    def _change_artifacts(path: Path, workspace: dict[str, Any], changes: ws.Changes, use_git: bool) -> list[ArtifactDraft]:
        drafts: list[ArtifactDraft] = []
        if changes.count:
            note = None
            if changes.pre_existing:
                note = f"{len(changes.pre_existing)} file(s) were already modified before this task and were left as they were."
            drafts.append(
                ArtifactDraft(
                    type="structured",
                    title="Changed files",
                    summary=note or f"{changes.count} file(s) in {workspace.get('name', 'the workspace')}",
                    payload={"columns": ["File", "Change"], "rows": changes.to_rows()},
                    metadata={"workspace_id": workspace.get("id"), "workspace_name": workspace.get("name"), "files": changes.touched, "pre_existing": changes.pre_existing},
                )
            )
            if use_git:
                diff = ws.git_diff(path, changes)
                if diff:
                    drafts.append(ArtifactDraft(type="diff", title="View changes", mime_type="text/x-diff", payload={"text": diff}, metadata={"workspace_id": workspace.get("id")}))
        remote = (workspace.get("repository") or {}).get("remote_url")
        if remote:
            drafts.append(ArtifactDraft(type="deep_link", title=f"Open {workspace.get('name', 'repository')}", external_url=str(remote)))
        return drafts


register_adapter(ClaudeCodeAdapter())
