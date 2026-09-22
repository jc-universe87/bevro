"""Creating an agent: intent in, an ordinary provider out.

    AgentSpec  →  a builder writes a project  →  Bevro's ordinary discovery
    finds how to run it  →  RuntimeProfile  →  Provider

The last two steps are the same ones Connect uses. Nothing here registers a
runtime by hand: if discovery cannot find a way in, the build has failed.

Like bridges, the work happens where the files are — the machine running the
worker — so this module is a state machine the worker turns.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.runtime import RuntimeKind, RuntimeProfile
from app.config import get_settings
from app.create.build import AGENT_MANIFEST, BuildSpec, build_prompt, find_tests, fingerprint, scan_project, serious, write_manifest
from app.create.spec import AgentSpec, Permission
from app.models import AgentBuild, Provider, Task
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.services import tasks as task_service
from app.services import workspaces as workspace_service

log = logging.getLogger("bevro.create")

# What a provider must declare to be asked to build an agent. Capability, never a name.
BUILDER_CAPABILITIES = ("agent_building", "coding", "repository_changes", "software_build")
NO_BUILDER_MESSAGE = "Creating agents needs a connected coding agent."
BUILD_FAILED_MESSAGE = "Bevro couldn't finish creating this agent."
SAMPLE_REQUEST = "Say what you do in one sentence, so Bevro can check you work."
TEST_TIMEOUT_S = 300

STATES = {
    "designing": "Designing…",
    "building": "Building…",
    "testing": "Testing…",
    "connecting": "Connecting…",
}
BUILD_STATE_IDS = tuple(STATES)
BUILD_STEPS = ["Designing", "Building", "Testing", "Connecting"]


class CreateError(Exception):
    def __init__(self, message: str, status: int = 409, *, recorded: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.recorded = recorded


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- builders

def builder_candidates(db: Session) -> list[Provider]:
    found: list[Provider] = []
    for capability in BUILDER_CAPABILITIES:
        for provider in provider_service.providers_with_capability(db, capability):
            if provider not in found and provider_service.is_available(provider):
                found.append(provider)
    return found


def builder_for(db: Session) -> Provider | None:
    return next(iter(builder_candidates(db)), None)


def can_build(db: Session) -> bool:
    return builder_for(db) is not None


# --------------------------------------------------------------------------- where agents live

def agent_dir(provider_id: uuid.UUID | str) -> Path:
    return Path(get_settings().agents_dir) / str(provider_id)


def version_dir(provider_id: uuid.UUID | str, build_id: uuid.UUID | str) -> Path:
    return agent_dir(provider_id) / "versions" / str(build_id)


def workspace_reference(provider_id: uuid.UUID | str, build_id: uuid.UUID | str) -> str:
    return f"{workspace_service.AGENTS_PREFIX}{provider_id}/versions/{build_id}"


def point_current_at(provider_id: uuid.UUID | str, build_id: uuid.UUID | str) -> None:
    """`current` names the version in use. Swapped only after one validates."""
    root = agent_dir(provider_id)
    root.mkdir(parents=True, exist_ok=True)
    link = root / "current"
    target = Path("versions") / str(build_id)
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target, target_is_directory=True)
    except OSError:  # a filesystem without links: a plain note is enough
        (root / "current.txt").write_text(str(target) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- state

def _state_runtime(state: str, warnings: list[str] | None = None) -> RuntimeProfile:
    return RuntimeProfile(
        id=state,
        kind=RuntimeKind.UNKNOWN,
        display_name=STATES.get(state, "Preparing agent…"),
        confidence="low",
        availability="not_invocable",
        adapter={},
        warnings=warnings or [],
    )


def build_state(provider: Provider) -> str | None:
    active = runtime_service.active_runtime(provider)
    return active.id if active is not None and active.id in BUILD_STATE_IDS else None


def _set_state(db: Session, provider: Provider, build: AgentBuild, state: str) -> None:
    build.state = state
    if state in BUILD_STATE_IDS and not any(rt.invocable for rt in runtime_service.runtimes_of(provider)):
        # Nothing works yet: show the step. A rebuild of a working agent keeps
        # its runtime, so the person can still use it while the new one is made.
        keep = [rt for rt in runtime_service.runtimes_of(provider) if rt.id not in STATES]
        runtime_service.set_runtimes(provider, [*keep, _state_runtime(state)], state)
    db.commit()


def active_build(db: Session, provider: Provider) -> AgentBuild | None:
    return db.scalars(select(AgentBuild).where(AgentBuild.provider_id == provider.id, AgentBuild.active.is_(True))).first()


def latest_build(db: Session, provider: Provider) -> AgentBuild | None:
    return db.scalars(select(AgentBuild).where(AgentBuild.provider_id == provider.id).order_by(AgentBuild.build_number.desc())).first()


def pending_builds(db: Session) -> list[AgentBuild]:
    return list(db.scalars(select(AgentBuild).where(AgentBuild.state.in_(BUILD_STATE_IDS)).order_by(AgentBuild.created_at)))


# --------------------------------------------------------------------------- starting

def start_build(db: Session, spec: AgentSpec) -> tuple[Provider, AgentBuild]:
    """Create the provider and the first build. The worker does the work."""
    if builder_for(db) is None:
        raise CreateError(NO_BUILDER_MESSAGE, 503)
    provider = provider_service.register_provider(
        db,
        {
            "name": spec.name,
            "description": spec.description,
            "enabled": True,
            "capabilities": spec.capability_dicts(),
            "adapter": {"kind": "declared", "spec": {"source_description": spec.purpose or spec.description}},
            "runtimes": [_state_runtime("designing")],
            "active_runtime": "designing",
            "icon": {"kind": "letter", "text": spec.name[:1].upper()},
            "origin": "created",
        },
    )
    build = _new_build(db, provider, spec)
    db.commit()
    db.refresh(provider)
    log.info("create: %s is waiting to be built", provider.slug)
    return provider, build


def _new_build(db: Session, provider: Provider, spec: AgentSpec) -> AgentBuild:
    previous = latest_build(db, provider)
    build = AgentBuild(
        provider_id=provider.id,
        spec=spec.model_dump(mode="json"),
        spec_version=spec.version,
        build_number=(previous.build_number + 1) if previous else 1,
        state="designing",
    )
    db.add(build)
    db.flush()
    build.directory = workspace_reference(provider.id, build.id)
    db.flush()
    return build


def rebuild(db: Session, provider: Provider, spec: AgentSpec | None = None) -> AgentBuild:
    """Build a new version beside the working one. Nothing is replaced until it passes."""
    if provider.origin != "created":
        raise CreateError("This agent wasn't created by Bevro.")
    if builder_for(db) is None:
        raise CreateError(NO_BUILDER_MESSAGE, 503)
    current = latest_build(db, provider)
    if current is not None and current.state in BUILD_STATE_IDS:
        raise CreateError("This agent is already being built.")
    if spec is None:
        spec = AgentSpec.model_validate((current.spec if current else None) or {})
    build = _new_build(db, provider, spec)
    if spec.name and spec.name != provider.name:
        provider.name = spec.name[:120]
    provider.description = spec.description
    provider.capabilities = spec.capability_dicts()
    db.commit()
    log.info("create: %s will be rebuilt (build %s)", provider.slug, build.build_number)
    return build


# --------------------------------------------------------------------------- what the worker does

def run_pending(db: Session, roots: list[Path] | None = None) -> int:
    """Worker entry point: carry each waiting build to its next step."""
    handled = 0
    for build in pending_builds(db):
        provider = provider_service.get_provider(db, build.provider_id)
        if provider is None:
            build.state = "failed"
            db.commit()
            continue
        try:
            if build.state == "designing":
                _submit(db, provider, build)
                handled += 1
            elif build.state in ("building", "testing", "connecting"):
                handled += 1 if _follow(db, provider, build) else 0
        except CreateError as exc:
            if not exc.recorded:
                _fail(db, provider, build, str(exc))
            handled += 1
        except Exception:  # noqa: BLE001 - one bad build must not stop the worker
            log.exception("create: building %s crashed", provider.slug)
            _fail(db, provider, build, "something went wrong while building this agent")
            handled += 1
    return handled


def _submit(db: Session, provider: Provider, build: AgentBuild) -> Task:
    builder = builder_for(db)
    if builder is None:
        raise CreateError(NO_BUILDER_MESSAGE)
    spec = AgentSpec.model_validate(build.spec)
    folder = version_dir(provider.id, build.id)
    folder.mkdir(parents=True, exist_ok=True)
    build_spec = BuildSpec(provider_id=str(provider.id), build_id=str(build.id), spec=spec, target_dir=str(folder))
    workspace = workspace_service.create_internal(
        db,
        slug=f"agent-{build.id}",
        name=f"Building {spec.name}",
        path=build.directory,
        read_paths=[],
        description="Bevro's own folder for an agent it is building",
    )
    task = task_service.submit(
        db,
        build_prompt(build_spec),
        provider=builder,
        input={"workspace_id": str(workspace.id)},
        trusted=True,
        title=f"Creating {spec.name}",
    )
    task.routing = {**(task.routing or {}), "create": {"provider_id": str(provider.id), "build_id": str(build.id), "builder": builder.slug}}
    build.task_id = task.id
    build.builder = builder.slug
    _set_state(db, provider, build, "building")
    log.info("create: %s is building %s (task %s)", builder.slug, provider.slug, task.id)
    return task


def _follow(db: Session, provider: Provider, build: AgentBuild) -> bool:
    task = db.get(Task, build.task_id) if build.task_id else None
    if task is None:
        _fail(db, provider, build, "the building task was lost")
        return True
    if task.state not in ("completed", "failed", "cancelled"):
        return False
    if task.state != "completed":
        _fail(db, provider, build, "your coding agent couldn't finish the build" if task.state == "failed" else "the build was stopped")
        return True
    _set_state(db, provider, build, "testing")
    validate_and_activate(db, provider, build)
    return True


# --------------------------------------------------------------------------- validation, then ordinary discovery

def validate_and_activate(db: Session, provider: Provider, build: AgentBuild) -> RuntimeProfile:
    """Check what was built, discover how to run it, and only then activate it."""
    folder = version_dir(provider.id, build.id)
    checks: list[str] = []
    spec = AgentSpec.model_validate(build.spec)

    if not folder.is_dir() or not any(folder.iterdir()):
        _fail(db, provider, build, "nothing was built")
        raise CreateError(BUILD_FAILED_MESSAGE, recorded=True)

    # 1. Nothing dangerous in the source.
    findings = scan_project(folder)
    if serious(findings):
        detail = "; ".join(f"{f.file}: {f.problem}" for f in serious(findings)[:3])
        _fail(db, provider, build, f"the generated code was refused ({detail})", checks=["security scan"])
        raise CreateError(BUILD_FAILED_MESSAGE, recorded=True)
    checks.append("security scan")

    # 2. Its own tests, if it has any.
    command = find_tests(folder)
    ran = False
    if command is not None:
        ok, detail = _run_tests(folder, command)
        if not ok:
            _fail(db, provider, build, f"its own tests did not pass ({detail})", checks=checks)
            raise CreateError(BUILD_FAILED_MESSAGE, recorded=True)
        ran = detail is not None
    # Tests this machine cannot run are not held against the agent; discovery
    # and a real request still have to pass before anything is activated.
    checks.append("its own tests" if ran else "tests not run on this machine")

    # 3. Bevro's ordinary discovery, exactly as for a project someone connects.
    _set_state(db, provider, build, "connecting")
    draft = _discover(folder)
    if draft is None or not any(rt.invocable for rt in draft.runtimes):
        _fail(db, provider, build, "Bevro couldn't find a way to run what was built", checks=checks)
        raise CreateError(BUILD_FAILED_MESSAGE, recorded=True)
    checks.append("discovery")

    # 4. One safe sample request through the runtime discovery chose. An agent
    # that only wants a credential is not a failed build: it is one that needs
    # an account connecting, which the ordinary credential handling asks for.
    runtime = next(rt for rt in draft.runtimes if rt.invocable)
    ok, detail, needs_credential = _probe(provider, runtime, folder)
    if not ok and not needs_credential:
        _fail(db, provider, build, f"it did not answer a sample request ({detail})", checks=checks)
        raise CreateError(BUILD_FAILED_MESSAGE, recorded=True)
    checks.append("sample request" if ok else "sample request (waiting for a credential)")

    # 5. Activate: capabilities stay the ones agreed with the person.
    write_manifest(folder, provider_id=str(provider.id), build_id=str(build.id), spec=spec, builder=build.builder or "a coding agent", runtime=runtime.kind.value, validation="passed")
    runtime_service.set_runtimes(provider, list(draft.runtimes), draft.active_runtime)
    provider.capabilities = spec.capability_dicts()
    provider.description = spec.description
    provider.availability = None
    provider.source = {"kind": "created", "target": str(folder)}
    point_current_at(provider.id, build.id)
    for other in db.scalars(select(AgentBuild).where(AgentBuild.provider_id == provider.id)):
        other.active = other.id == build.id
    build.state = "ready"
    build.validation = {"ok": True, "checks": checks, "runtime": runtime.kind.value, "needs_credential": not ok, "warnings": [f"{f.file}: {f.problem}" for f in findings if not f.serious]}
    build.source_fingerprint = fingerprint(folder)
    db.commit()
    log.info("create: %s is ready (build %s, %s)", provider.slug, build.build_number, runtime.kind.value)
    return runtime


def _discover(folder: Path):
    """Bevro's own discovery, pointed at the folder that was just built."""
    from app.connect.service import get_discovery_service
    from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed
    from app.connect.targets import classify_target

    try:
        return get_discovery_service().discover(classify_target(str(folder)), DiscoveryContext(roots=[folder.parent]))
    except DiscoveryFailed as exc:
        log.info("create: discovery found nothing usable (%s)", exc)
        return None


def _probe(provider: Provider, runtime: RuntimeProfile, folder: Path) -> tuple[bool, str, bool]:
    """(answered, what went wrong, it only needs a credential)."""
    from adapters import FailureKind, InvocationRequest

    try:
        adapter = runtime_service.adapter_for(runtime)
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__, False
    spec = runtime_service.spec_for(provider, runtime).model_copy(update={"adapter": runtime.adapter})
    request = InvocationRequest(task_id="create-check", run_id="create-check", request=SAMPLE_REQUEST, input={"integration_dir": str(folder)})
    try:
        result = adapter.invoke(spec, request)
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__, False
    if result.failure == FailureKind.CREDENTIAL_REQUIRED:
        return False, result.error or "it needs a credential", True
    if result.state.value not in ("completed", "needs_input"):
        return False, result.error or result.state.value, False
    return True, "", False


# What a runner says when it found nothing to run. Not a failing test: the
# project's tests are simply written for a runner this machine does not have.
_NOTHING_TO_RUN = ("NO TESTS RAN", "no tests ran", "no tests collected", "collected 0 items")


def _run_tests(folder: Path, command: list[str]) -> tuple[bool, str | None]:
    """(passed, why not). `None` as the reason means they could not be run here."""
    program = shutil.which(command[0])
    if program is None:
        return True, None
    try:
        out = subprocess.run([program, *command[1:]], cwd=folder, capture_output=True, text=True, timeout=TEST_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return True, None
    text = f"{out.stdout}\n{out.stderr}"
    if out.returncode == 5 or any(phrase in text for phrase in _NOTHING_TO_RUN):
        return True, None
    if out.returncode != 0:
        tail = [line for line in (out.stdout or out.stderr or "").strip().splitlines() if line.strip()]
        return False, tail[-1][:160] if tail else f"exit {out.returncode}"
    return True, ""


def _fail(db: Session, provider: Provider, build: AgentBuild, reason: str, checks: list[str] | None = None) -> None:
    """Record why, and leave any working version exactly as it was."""
    log.warning("create: %s build %s failed: %s", provider.slug, build.build_number, reason)
    build.state = "failed"
    build.validation = {"ok": False, "reason": reason, "checks": checks or []}
    keep = [rt for rt in runtime_service.runtimes_of(provider) if rt.id not in STATES]
    if keep:
        # A rebuild that failed: the version in use carries on untouched.
        runtime_service.set_runtimes(provider, keep, None)
    else:
        runtime_service.set_runtimes(provider, [_state_runtime("designing", [BUILD_FAILED_MESSAGE])], "designing")
        runtimes = runtime_service.runtimes_of(provider)
        runtimes[0].display_name = "Not built"
        runtime_service.set_runtimes(provider, runtimes, runtimes[0].id)
    db.commit()


# --------------------------------------------------------------------------- what the browser sees

def status(db: Session, provider: Provider) -> dict[str, Any]:
    build = latest_build(db, provider)
    if build is None:
        return {"state": "failed", "note": BUILD_FAILED_MESSAGE, "provider_id": str(provider.id), "task_id": None, "steps": []}
    if build.state in BUILD_STATE_IDS:
        return {
            "state": build.state,
            "note": STATES[build.state],
            "provider_id": str(provider.id),
            "task_id": str(build.task_id) if build.task_id else None,
            "steps": BUILD_STEPS[: BUILD_STATE_IDS.index(build.state) + 1],
        }
    if build.state == "ready":
        return {"state": "ready", "note": "Created.", "provider_id": str(provider.id), "task_id": str(build.task_id) if build.task_id else None, "steps": BUILD_STEPS}
    reason = (build.validation or {}).get("reason")
    active = active_build(db, provider)
    note = BUILD_FAILED_MESSAGE if active is None else "Bevro couldn't build the new version, so the one you have is still in use."
    return {"state": "failed", "note": note, "detail": reason, "provider_id": str(provider.id), "task_id": str(build.task_id) if build.task_id else None, "steps": []}


def summary(db: Session, provider: Provider) -> dict[str, Any] | None:
    """What Manage shows for an agent Bevro created. No source, no paths."""
    if provider.origin != "created":
        return None
    build = latest_build(db, provider)
    active = active_build(db, provider)
    if build is None:
        return None
    spec = AgentSpec.model_validate((active or build).spec or {})
    return {
        "purpose": spec.purpose or spec.description,
        "version": (active or build).build_number,
        "state": build.state,
        "built_by": (active or build).builder,
        "needs": [p.value for p in spec.permissions],
        "can_rebuild": True,
    }
