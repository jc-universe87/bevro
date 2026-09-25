"""Command adapter: the provider is a program Bevro starts for each task.

This is how an existing agent with a command-line entry point is used without
changing it. Bevro keeps the *adapter profile* (working directory, launch
command, how the request goes in, how the answer comes out) on its own side.

config:
  argv            - the command as a list; never a shell string
  cwd             - working directory (server-side path inside an approved
                    root), or absent: Bevro's own integration directory
  input           - {"mode": "flag", "flag": "--topic"}   request as a named option
                    {"mode": "argument"}                   request as the last argument
                    {"mode": "stdin"}                      request on standard input
                    {"mode": "stdin", "format": "json"}    {"request": ..., "context": {...}} on standard input
                    {"mode": "file", "flag": "--input"}    request written to a file, its path passed
                    {"mode": "environment", "name": "REQUEST"}  request in an environment variable
                    {"mode": "none"}                       the program takes no input
  output          - {"modes": ["json"]}: the program prints one JSON object
                    {"status", "summary", "artifacts": [...]} - the contract an
                    integration bridge speaks; or
                    {"modes": ["stdout", "files", "report_dir"], "report_dir": "reports"}
                    stdout: what the program prints becomes the report;
                    files: files it names inside its own folder are attached;
                    report_dir: files that appear under that folder during
                    the run are attached. (Old form {"mode": "stdout"} = stdout + files.)
  env             - extra non-secret environment variables
  secret_env      - names of stored secrets to pass as environment variables
  timeout_seconds - default 1800

Runs in the worker (execution = "background"): the command lives on the host.
No shell is ever involved, so nothing in a request can be interpreted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

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
from adapters.localroots import OutsideRoots, resolve_within
from adapters.registry import register_adapter
from adapters.runtime import BaseRuntimeAdapter

DEFAULT_TIMEOUT_S = 1800
CANCEL_GRACE_S = 5.0
MAX_OUTPUT_CHARS = 200_000
MAX_REPORT_CHARS = 20_000
MAX_ATTACHED_FILES = 5
MAX_FILE_BYTES = 5_000_000
# A program's own self-check, when it declares one, is run by Test. Short,
# because a check that takes minutes is doing real work.
SELF_CHECK_TIMEOUT_S = 30
TEXT_SUFFIXES = {".md", ".txt", ".markdown"}
FILE_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".json", ".html", ".csv"}

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# What a program's last words say about why it stopped. Used for the failure
# category only; the text itself never leaves the server.
_CREDENTIAL_RE = re.compile(r"(OPENAI|ANTHROPIC|GOOGLE|MISTRAL|COHERE|GROQ)_API_KEY|api[_ -]?key|AuthenticationError|OpenAIError|Incorrect API key|invalid_api_key|401 Unauthorized|Unauthorized|PermissionDenied", re.I)
_CONFIG_RE = re.compile(r"ModuleNotFoundError|No module named|ImportError|command not found|No such file or directory|cannot open|SyntaxError", re.I)
_PATH_RE = re.compile(r"(?<![\w/.-])((?:~|\.{1,2})?/?(?:[\w.-]+/)*[\w.-]+\.(?:md|markdown|txt|pdf|json|html|csv))\b")


def strip_noise(text: str, limit: int = MAX_REPORT_CHARS) -> str:
    cleaned = _CTRL_RE.sub("", _ANSI_RE.sub("", text or ""))
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "\n\n[shortened]"
    return cleaned.strip()


class CommandUnavailable(Exception):
    pass


def resolve_cwd(config: dict[str, Any], request_input: dict[str, Any] | None = None) -> Path:
    """The working directory: an approved server-side path, or Bevro's own integration folder."""
    raw = config.get("cwd")
    if raw:
        try:
            return resolve_within(str(raw))
        except OutsideRoots as exc:
            raise CommandUnavailable(f"working directory: {exc}") from exc
    integration = (request_input or {}).get("integration_dir")
    if not integration:
        raise CommandUnavailable("no working directory")
    path = Path(str(integration))
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def resolve_executable(argv: list[str], cwd: Path) -> str:
    """argv[0] must be a program on PATH (by name or by its full path), or a file inside the working directory / an approved root."""
    if not argv or not str(argv[0]).strip():
        raise CommandUnavailable("empty command")
    exe = str(argv[0])
    if "/" in exe or exe.startswith("~"):
        candidate = Path(os.path.expanduser(exe))
        if not candidate.is_absolute():
            candidate = cwd / candidate
        # Normalise ".." without following symlinks: a virtual environment's
        # python is a symlink out of the project by design, and is run via
        # its own path so the venv is honoured.
        normalised = Path(os.path.normpath(candidate))
        inside_cwd = normalised == cwd or cwd in normalised.parents
        on_path = str(normalised.parent) in os.environ.get("PATH", "").split(os.pathsep)
        if not inside_cwd and not on_path:
            try:
                resolve_within(str(normalised.parent))
            except OutsideRoots as exc:
                raise CommandUnavailable(f"program is outside the approved folders: {normalised.name}") from exc
        if not normalised.is_file():
            raise CommandUnavailable(f"program not found: {normalised.name}")
        if not os.access(normalised, os.X_OK):
            raise CommandUnavailable(f"program is not executable: {normalised.name}")
        return str(normalised)
    found = shutil.which(exe)
    if found is None:
        raise CommandUnavailable(f"program not found on this machine: {exe}")
    return found


def build_argv(config: dict[str, Any], request_text: str, cwd: Path, scratch: Path | None = None) -> tuple[list[str], str | None, dict[str, str]]:
    """(argv, stdin text, extra environment). The request is always one value, never interpolated."""
    argv = [str(a) for a in config.get("argv") or []]
    argv[0] = resolve_executable(argv, cwd)
    spec = config.get("input") or {}
    mode = str(spec.get("mode") or "argument")
    stdin_text: str | None = None
    extra_env: dict[str, str] = {}
    if mode == "flag":
        flag = str(spec.get("flag") or "").strip()
        if not flag.startswith("-"):
            raise CommandUnavailable("input flag is not configured")
        argv += [flag, request_text]
    elif mode == "argument":
        argv.append(request_text)
    elif mode == "stdin":
        if str(spec.get("format") or "text") == "json":
            stdin_text = json.dumps({"request": request_text, "context": {}}) + "\n"
        else:
            stdin_text = request_text if request_text.endswith("\n") else request_text + "\n"
    elif mode == "file":
        # The request goes into Bevro's own scratch folder, never into the project.
        folder = scratch or Path(tempfile.mkdtemp(prefix="bevro-request-"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "request.txt"
        path.write_text(request_text, encoding="utf-8")
        flag = str(spec.get("flag") or "").strip()
        argv += [flag, str(path)] if flag.startswith("-") else [str(path)]
    elif mode == "environment":
        name = str(spec.get("name") or "BEVRO_REQUEST")
        extra_env[name] = request_text
    elif mode != "none":
        raise CommandUnavailable(f"unknown input mode {mode!r}")
    return argv, stdin_text, extra_env


def build_env(config: dict[str, Any], secrets: dict[str, str], extra: dict[str, str] | None = None) -> dict[str, str]:
    """The program's environment: the worker's own (so an agent that expects its key
    there finds it), the profile's non-secret extras, the request when it travels
    as a variable, and finally any value Bevro holds for a named secret, which
    deliberately overrides the inherited one."""
    env = os.environ.copy()
    env.setdefault("TERM", "dumb")
    env.setdefault("PYTHONUNBUFFERED", "1")
    for key, value in (config.get("env") or {}).items():
        env[str(key)] = str(value)
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    for name in config.get("secret_env") or []:
        if name in secrets:
            env[str(name)] = secrets[name]
    return env


def credential_sources(config: dict[str, Any], secrets: dict[str, str]) -> dict[str, str]:
    """For each credential the profile names, where it will come from, in order of
    precedence: a value Bevro holds ("bevro", an explicit override), the host
    environment ("host"), the project's own .env ("project"), or nowhere ("missing")."""
    self_configured = {str(n) for n in config.get("self_configured") or []}
    out: dict[str, str] = {}
    for raw in config.get("secret_env") or []:
        name = str(raw)
        if name in secrets:
            out[name] = "bevro"
        elif name in os.environ:
            out[name] = "host"
        elif name in self_configured:
            out[name] = "project"
        else:
            out[name] = "missing"
    return out


def entry_present(argv: list[str], cwd: Path, config: dict[str, Any]) -> bool | None:
    """Is what the program is asked to run actually there? None when there is nothing to look for.

    `python -m package.module` is looked for under the folder and anything
    the profile puts on PYTHONPATH; `python script.py` as a file. Nothing is
    imported.
    """
    if "-m" in argv:
        at = argv.index("-m")
        if at + 1 >= len(argv):
            return False
        rel = Path(*argv[at + 1].split("."))
        roots = [cwd, *(Path(p) for p in str((config.get("env") or {}).get("PYTHONPATH") or "").split(os.pathsep) if p)]
        return any(root.joinpath(rel).with_suffix(".py").is_file() or (root / rel / "__init__.py").is_file() or (root / rel / "__main__.py").is_file() for root in roots)
    if len(argv) > 1 and argv[1].endswith((".py", ".js", ".mjs", ".sh")) and not argv[1].startswith("-"):
        return (cwd / argv[1]).is_file()
    return None


def missing_secrets(config: dict[str, Any], secrets: dict[str, str]) -> list[str]:
    """Credentials that neither Bevro, the host environment nor the project itself can supply."""
    return [name for name, source in credential_sources(config, secrets).items() if source == "missing"]


def classify_exit(stderr_tail: str) -> FailureKind:
    """A non-zero exit, read from the last lines of stderr. Conservative."""
    if _CREDENTIAL_RE.search(stderr_tail):
        return FailureKind.CREDENTIAL_REQUIRED
    if _CONFIG_RE.search(stderr_tail):
        return FailureKind.CONFIGURATION_PROBLEM
    return FailureKind.INVOCATION_FAILED


# The small structured contract a program may answer with (an integration
# bridge does). Anything else in the object is ignored.
JSON_STATUSES = {"completed": ResultState.COMPLETED, "failed": ResultState.FAILED, "needs_input": ResultState.NEEDS_INPUT, "needs_approval": ResultState.NEEDS_APPROVAL}
JSON_FAILURES = {
    "configuration_problem": FailureKind.CONFIGURATION_PROBLEM,
    "credential_required": FailureKind.CREDENTIAL_REQUIRED,
    "provider_unavailable": FailureKind.PROVIDER_UNAVAILABLE,
    "invocation_failed": FailureKind.INVOCATION_FAILED,
    "timed_out": FailureKind.TIMED_OUT,
    "output_invalid": FailureKind.OUTPUT_INVALID,
}


def parse_json_output(stdout: str) -> dict[str, Any] | None:
    """The last JSON object a program printed, or None. Programs often log first."""
    text = strip_noise(stdout, MAX_OUTPUT_CHARS).strip()
    if not text:
        return None
    for candidate in (text, text[text.rfind("\n{") + 1:] if "\n{" in text else ""):
        if not candidate.strip().startswith("{"):
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def json_artifacts(data: dict[str, Any], cwd: Path) -> list[ArtifactDraft]:
    """The contract's artifacts as ordinary Bevro artifacts. Files must live
    inside the program's own folder."""
    drafts: list[ArtifactDraft] = []
    for item in (data.get("artifacts") or [])[:MAX_ATTACHED_FILES + 5]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "report")
        title = " ".join(str(item.get("title") or "Result").split())[:120]
        if kind == "file" and item.get("path"):
            candidate = Path(str(item["path"]))
            if not candidate.is_absolute():
                candidate = cwd / candidate
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not (resolved == cwd or cwd in resolved.parents) or not resolved.is_file():
                continue
            drafts += file_artifacts([resolved], cwd)
        elif kind in ("deep_link", "link") and item.get("url"):
            url = str(item["url"])
            if url.startswith(("http://", "https://")):
                drafts.append(ArtifactDraft(type="deep_link", title=title, external_url=url))
        elif kind in ("structured", "table", "data") and item.get("payload") is not None:
            payload = item["payload"]
            if isinstance(payload, (dict, list)):
                drafts.append(ArtifactDraft(type="structured", title=title, payload=payload))
        else:
            text = str(item.get("text") or item.get("content") or "")
            if text.strip():
                drafts.append(ArtifactDraft(type="report", title=title, mime_type="text/markdown", payload={"text": text[:100_000]}))
    return drafts


def output_modes(config: dict[str, Any]) -> tuple[set[str], str | None]:
    """(modes, report directory) from the profile; the old {"mode": "stdout"} means stdout + files."""
    spec = config.get("output") or {}
    modes = {str(m) for m in spec.get("modes") or []} if spec.get("modes") else {"stdout", "files"}
    report_dir = str(spec.get("report_dir")) if spec.get("report_dir") else None
    if report_dir:
        modes.add("report_dir")
    return modes, report_dir


def snapshot_dir(folder: Path) -> dict[str, tuple[int, int]]:
    """rel path -> (size, mtime) under a report directory, shallowly bounded."""
    out: dict[str, tuple[int, int]] = {}
    if not folder.is_dir():
        return out
    for path in sorted(folder.rglob("*")):
        if path.is_file() and len(out) < 2000:
            try:
                st = path.stat()
            except OSError:
                continue
            out[str(path.relative_to(folder))] = (st.st_size, st.st_mtime_ns)
    return out


def summary_line(stdout: str, name: str) -> str:
    lines = [ln.strip() for ln in strip_noise(stdout, MAX_OUTPUT_CHARS).splitlines() if ln.strip()]
    if not lines:
        return f"Done. {name} finished without printing anything."
    last = lines[-1].lstrip("#*- ").strip()
    if len(last) > 200:
        last = last[:200].rsplit(" ", 1)[0] + "…"
    return last if last.endswith((".", "!", "?")) else last + "."


def mentioned_files(stdout: str, cwd: Path) -> list[Path]:
    """Files the program named in its output that exist inside its own folder."""
    found: list[Path] = []
    for match in _PATH_RE.finditer(stdout):
        raw = match.group(1)
        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.suffix.lower() not in FILE_SUFFIXES or not resolved.is_file():
            continue
        if not (resolved == cwd or cwd in resolved.parents):
            continue
        if resolved in found:
            continue
        found.append(resolved)
        if len(found) >= MAX_ATTACHED_FILES:
            break
    return found


def file_artifacts(paths: list[Path], cwd: Path) -> list[ArtifactDraft]:
    drafts: list[ArtifactDraft] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        rel = str(path.relative_to(cwd))
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            drafts.append(ArtifactDraft(type="report", title=path.name, mime_type="text/markdown", payload={"text": text[:100_000]}, metadata={"path": rel}))
        else:
            try:
                content = path.read_bytes()
            except OSError:
                continue
            mime = {".pdf": "application/pdf", ".json": "application/json", ".html": "text/html", ".csv": "text/csv"}.get(path.suffix.lower())
            drafts.append(ArtifactDraft(type="file", title=path.name, mime_type=mime, content=content, filename=path.name, metadata={"path": rel}))
    return drafts


class CommandAdapter(BaseRuntimeAdapter):
    kind = "command"
    execution = "background"

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    # -- health -------------------------------------------------------------
    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult:
        """Is the folder approved and the program present? Nothing is run."""
        cfg = provider.adapter_config
        argv = [str(a) for a in cfg.get("argv") or []]
        try:
            if cfg.get("cwd"):
                cwd = resolve_cwd(cfg)
            else:
                # Runs in Bevro's own integration folder; only a program on PATH or an absolute one can be checked here.
                cwd = Path(os.path.expanduser("~"))
            resolve_executable(argv, cwd)
        except CommandUnavailable as exc:
            return HealthResult(ok=False, state="unavailable", detail=str(exc), credentials=credential_sources(cfg, secrets))
        return HealthResult(ok=True, state="available", credentials=credential_sources(cfg, secrets))

    def readiness(self, provider: ProviderSpec) -> list[dict[str, Any]]:
        """What can be proved without running anything, one fact at a time.

        Each is {"label", "ok"}; the first thing missing ends the list,
        because nothing after it could be true. Nothing is run and nothing
        raw is returned: no paths, no program output.
        """
        cfg = provider.adapter_config
        argv = [str(a) for a in cfg.get("argv") or []]
        steps: list[dict[str, Any]] = []
        try:
            cwd = resolve_cwd(cfg) if cfg.get("cwd") else Path(os.path.expanduser("~"))
        except CommandUnavailable:
            return [{"label": "Bevro can't open its folder", "ok": False}]
        if cfg.get("cwd"):
            steps.append({"label": "Found it on this machine", "ok": True})
        python = bool(argv) and Path(argv[0]).name.startswith("python")
        try:
            resolve_executable(argv, cwd)
        except CommandUnavailable:
            steps.append({"label": "Its Python environment is missing" if python else "The program it runs isn't installed", "ok": False})
            return steps
        steps.append({"label": "Its Python environment is available" if python else "The program it runs is installed", "ok": True})
        entry = entry_present(argv, cwd, cfg)
        if entry is not None:
            steps.append({"label": "Found the command Bevro will use" if entry else "The command Bevro would use isn't there", "ok": entry})
        return steps

    def self_check(self, provider: ProviderSpec, secrets: dict[str, str]) -> dict[str, Any] | None:
        """Run the program's own self-check, when it declares one. None when it doesn't.

        Only an option whose name promises a check and nothing else is ever
        used (see SELF_CHECK_OPTIONS); a dry run, which may still fetch or
        send things, is not one.
        """
        cfg = provider.adapter_config
        option = [str(a) for a in cfg.get("self_check") or []]
        if not option:
            return None
        argv = [str(a) for a in cfg.get("argv") or []]
        try:
            cwd = resolve_cwd(cfg)
            argv[0] = resolve_executable(argv, cwd)
            done = subprocess.run([*argv, *option], cwd=cwd, env=build_env(cfg, secrets), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SELF_CHECK_TIMEOUT_S)
        except (CommandUnavailable, OSError, subprocess.TimeoutExpired):
            return {"label": "Its own self-check didn't finish", "ok": False, "kind": "functional"}
        ok = done.returncode == 0
        return {"label": "Its own self-check passed" if ok else "Its own self-check failed", "ok": ok, "kind": "functional"}

    # -- invoke ---------------------------------------------------------------
    def invoke(self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult:
        context = context or InvocationContext()
        cfg = provider.adapter_config
        missing = missing_secrets(cfg, request.secrets)
        if missing:
            # Do not even start: the program would only fail, and might leave half-made files behind.
            return InvocationResult(
                state=ResultState.FAILED,
                error=f"{provider.name} needs a credential before it can run.",
                failure=FailureKind.CREDENTIAL_REQUIRED,
                metadata={"missing_secrets": missing},
            )
        try:
            cwd = resolve_cwd(cfg, request.input)
            scratch = Path(str(request.input["integration_dir"])) / "requests" / request.run_id if request.input.get("integration_dir") else None
            argv, stdin_text, extra_env = build_argv(cfg, request.request, cwd, scratch)
        except CommandUnavailable as exc:
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} could not start.", failure=FailureKind.CONFIGURATION_PROBLEM, metadata={"detail": str(exc)})
        modes, report_dir = output_modes(cfg)
        report_root = (cwd / report_dir) if report_dir else None
        report_before = snapshot_dir(report_root) if report_root else {}

        log_path = self._log_path(context, request)
        stderr_path = log_path.with_suffix(".stderr") if log_path else Path(tempfile.mkstemp(prefix="bevro-stderr-", suffix=".log")[1])
        context.progress(f"Running {provider.name}")
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_path.open("w"),
                text=True,
                env=build_env(cfg, request.secrets, extra_env),
                start_new_session=True,
            )
        except OSError as exc:
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} could not start.", failure=FailureKind.CONFIGURATION_PROBLEM, metadata={"detail": str(exc)[:300]})
        with self._lock:
            self._procs[request.run_id] = proc
        if stdin_text is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except OSError:
                pass

        collected: list[str] = []
        size = 0
        cancelled = False
        timed_out = False
        deadline = time.monotonic() + float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_S))
        log = log_path.open("a") if log_path else None
        try:
            for line in self._lines(proc, context, deadline):
                if line is None:
                    cancelled = True
                    timed_out = time.monotonic() > deadline and not context.cancelled()
                    break
                if log:
                    log.write(line)
                if size < MAX_OUTPUT_CHARS:
                    collected.append(line)
                    size += len(line)
            proc.wait(timeout=30)
        finally:
            if log:
                log.close()
            with self._lock:
                self._procs.pop(request.run_id, None)
            if proc.poll() is None:
                self._terminate(proc)

        stdout = "".join(collected)
        meta: dict[str, Any] = {"exit_code": proc.returncode, "argv0": Path(argv[0]).name}
        stderr_tail = self._tail(stderr_path, keep=log_path is not None)
        if timed_out:
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} took too long and was stopped.", failure=FailureKind.TIMED_OUT, metadata={**meta, "timed_out": True})
        if cancelled:
            return InvocationResult(state=ResultState.CANCELLED, error="The task was stopped before completion.", failure=FailureKind.CANCELLED, metadata=meta)
        if proc.returncode != 0:
            failure = classify_exit(stderr_tail)
            meta["failure_hint"] = stderr_tail.strip().splitlines()[-1][:200] if stderr_tail.strip() else None
            if failure == FailureKind.CREDENTIAL_REQUIRED:
                error = f"{provider.name} couldn't use its credential." if cfg.get("secret_env") and not missing else f"{provider.name} needs a credential before it can run."
            elif failure == FailureKind.CONFIGURATION_PROBLEM:
                error = f"{provider.name} couldn't be started with its current connection."
            else:
                error = f"{provider.name} started but couldn't finish this task."
            return InvocationResult(state=ResultState.FAILED, error=error, failure=failure, metadata=meta, artifacts=self._report(stdout, provider.name))
        context.progress("Collecting the result")
        if "json" in modes:
            data = parse_json_output(stdout)
            if data is None:
                return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} answered, but Bevro couldn't read the result.", failure=FailureKind.OUTPUT_INVALID, metadata=meta)
            state = JSON_STATUSES.get(str(data.get("status") or "completed"), ResultState.COMPLETED)
            summary = " ".join(str(data.get("summary") or "").split())[:300]
            if state == ResultState.FAILED:
                return InvocationResult(state=state, error=summary or f"{provider.name} couldn't complete this request.", failure=JSON_FAILURES.get(str(data.get("failure") or ""), FailureKind.INVOCATION_FAILED), metadata=meta)
            meta["output"] = {"stdout": "", "cwd": str(cwd), "report_dir": None, "report_before": {}, "modes": ["json"], "json": data}
            return InvocationResult(state=state, summary=summary or "Done.", artifacts=[], metadata=meta)
        # Output normalisation happens in collect_artifacts(); invoke() records what it saw.
        meta["output"] = {"stdout": stdout if "stdout" in modes else "", "cwd": str(cwd), "report_dir": str(report_root) if report_root else None, "report_before": report_before, "modes": sorted(modes)}
        return InvocationResult(state=ResultState.COMPLETED, summary=summary_line(stdout, provider.name), artifacts=[], metadata=meta)

    # -- output normalisation -------------------------------------------------
    def collect_artifacts(self, provider: ProviderSpec, result: InvocationResult, context: InvocationContext | None = None) -> list[ArtifactDraft]:
        """Turn what the program left behind into Bevro artifacts: its output as a
        report, files it named, and new files under its report directory."""
        info = (result.metadata or {}).get("output")
        if not isinstance(info, dict):
            return list(result.artifacts)
        modes = set(info.get("modes") or [])
        cwd = Path(str(info.get("cwd") or "."))
        stdout = str(info.get("stdout") or "")
        artifacts: list[ArtifactDraft] = list(result.artifacts)
        if "json" in modes:
            artifacts += json_artifacts(info.get("json") or {}, cwd)
            result.metadata.pop("output", None)
            return artifacts
        if "stdout" in modes:
            artifacts += self._report(stdout, provider.name)
        files: list[Path] = []
        if "files" in modes:
            files += mentioned_files(stdout, cwd)
        if "report_dir" in modes and info.get("report_dir"):
            root = Path(str(info["report_dir"]))
            before = {k: tuple(v) for k, v in (info.get("report_before") or {}).items()}
            for rel, sig in snapshot_dir(root).items():
                if before.get(rel) != sig and root / rel not in files and len(files) < MAX_ATTACHED_FILES:
                    files.append(root / rel)
        artifacts += file_artifacts(files, cwd)
        result.metadata["files"] = [str(p.relative_to(cwd)) for p in files if cwd in p.parents]
        result.metadata.pop("output", None)  # raw output never reaches the run record
        return artifacts

    # -- status / cancel ------------------------------------------------------
    def get_status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        raise NotSupported("command providers report through the worker")

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool:
        with self._lock:
            proc = self._procs.get(external_ref)
        if proc is None:
            return False
        self._terminate(proc)
        return True

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _report(stdout: str, name: str) -> list[ArtifactDraft]:
        text = strip_noise(stdout)
        if not text:
            return []
        return [ArtifactDraft(type="report", title=f"Output from {name}", mime_type="text/markdown", payload={"text": text})]

    def _lines(self, proc: subprocess.Popen[str], context: InvocationContext, deadline: float):
        assert proc.stdout is not None
        pending: list[str | None] = []
        lock = threading.Lock()

        def pump() -> None:
            for raw in proc.stdout:  # type: ignore[union-attr]
                with lock:
                    pending.append(raw)
            with lock:
                pending.append(None)

        threading.Thread(target=pump, daemon=True).start()
        while True:
            with lock:
                batch, pending[:] = pending[:], []
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
    def _tail(path: Path, *, keep: bool, size: int = 4000) -> str:
        """The last bytes of stderr, for classification. Temporary files are removed."""
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - size))
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            text = ""
        if not keep:
            try:
                path.unlink()
            except OSError:
                pass
        return text

    @staticmethod
    def _log_path(context: InvocationContext, request: InvocationRequest) -> Path | None:
        if not context.log_dir:
            return None
        directory = Path(context.log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{request.run_id}.log"


register_adapter(CommandAdapter())
