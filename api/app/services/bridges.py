"""Building an integration bridge: ordinary Bevro work, handed to a coding provider.

    draft needs a bridge
        → a Provider is created, "Preparing connection…"
        → a BridgeSpec describes exactly what is wanted
        → a Task goes to whichever provider declares the capability
        → the provider writes into data/integrations/<provider-id>/ only
        → Bevro validates what came back and checks the project is untouched
        → a RuntimeProfile is registered and the provider becomes usable

Nothing here names a coding provider: it asks the registry for a capability.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from adapters.localroots import OutsideRoots, resolve_within
from adapters.runtime import RuntimeKind, RuntimeProfile
from app.config import get_settings
from app.connect import bridge as bridge_module
from app.connect.bridge import BridgeSpec, build_prompt, build_spec, changed_files, fingerprint, read_manifest, runtime_from_manifest, snapshot, write_manifest
from app.connect.draft import ProviderDraft
from app.connect.inspect import Project, readme_excerpt
from app.connect.strategies.docker import inspect_docker
from app.connect.strategies.node import inspect_node
from app.connect.strategies.python import inspect_python
from app.connect.strategies.scripts import inspect_scripts
from app.models import ConnectDraft, Provider, Task
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.services import tasks as task_service
from app.services import workspaces as workspace_service

log = logging.getLogger("bevro.bridges")

# What a provider must declare to be asked to build a connection. The first
# that any available provider offers is used; no provider is named anywhere.
BRIDGE_CAPABILITIES = ("integration_bridge_building", "coding")
NO_BUILDER_MESSAGE = "This project needs a small connection bridge, and no connected agent can build one yet."
BRIDGE_RUNTIME_ID = "bridge"
SAMPLE_REQUEST = "Say hello so Bevro can check this connection works."


class BridgeError(Exception):
    def __init__(self, message: str, status: int = 409, *, recorded: bool = False) -> None:
        super().__init__(message)
        self.status = status
        # True when the reason has already been filed against the provider, so
        # the loop above does not replace it with a more general sentence.
        self.recorded = recorded


# --------------------------------------------------------------------------- choosing a builder

def builder_candidates(db: Session) -> list[Provider]:
    """Providers that can build a connection, most specific capability first."""
    found: list[Provider] = []
    for capability in BRIDGE_CAPABILITIES:
        for provider in provider_service.providers_with_capability(db, capability):
            if provider not in found and provider_service.is_available(provider):
                found.append(provider)
    return found


def builder_for(db: Session) -> Provider | None:
    return next(iter(builder_candidates(db)), None)


def bridge_possible(db: Session) -> bool:
    return builder_for(db) is not None


# --------------------------------------------------------------------------- paths

def integration_dir(provider_id: uuid.UUID | str) -> Path:
    """Bevro's own folder for this provider, in *this* process's terms."""
    root = Path(get_settings().integrations_dir)
    return root / str(provider_id)


def workspace_reference(provider_id: uuid.UUID | str) -> str:
    """How the folder is named in the database: resolved by whoever runs it."""
    return f"{workspace_service.INTEGRATIONS_PREFIX}{provider_id}"


# --------------------------------------------------------------------------- the states a connection goes through

# A provider being connected this way carries one of these as its runtime, so
# the browser can follow along without knowing anything about bridges.
STATES = {
    "preparing": "Preparing connection…",
    "building": "Building connection…",
    "testing": "Testing connection…",
    "unbuilt": "No connection yet",
}
BUILD_STATE_IDS = ("preparing", "building", "testing")


def _state_runtime(state: str, warnings: list[str] | None = None) -> RuntimeProfile:
    return RuntimeProfile(
        id=state,
        kind=RuntimeKind.UNKNOWN,
        display_name=STATES[state],
        confidence="low",
        availability="not_invocable",
        adapter={},
        evidence=[],
        warnings=warnings or [],
    )


def build_state(provider: Provider) -> str | None:
    """Which step of building a connection this provider is at, if any."""
    active = runtime_service.active_runtime(provider)
    return active.id if active is not None and active.id in BUILD_STATE_IDS else None


def _set_state(db: Session, provider: Provider, state: str, warnings: list[str] | None = None) -> None:
    keep = [rt for rt in runtime_service.runtimes_of(provider) if rt.id not in STATES]
    runtimes = [*keep, _state_runtime(state, warnings)]
    runtime_service.set_runtimes(provider, runtimes, state)
    db.commit()


# --------------------------------------------------------------------------- starting a build

def start_build(db: Session, row: ConnectDraft) -> Provider:
    """Create the provider and mark it as one whose connection is being built.

    The work itself happens where the project is - on the machine running the
    worker - so this only sets the wheels turning.
    """
    if not row.draft:
        raise BridgeError("There is nothing to connect yet.")
    draft = ProviderDraft.model_validate(row.draft)
    if not draft.needs_bridge:
        raise BridgeError("This project already has a connection Bevro can use.")
    if builder_for(db) is None:
        raise BridgeError(NO_BUILDER_MESSAGE, 503)
    if row.provider_id:
        raise BridgeError("This has already been connected.")

    provider = provider_service.register_provider(
        db,
        {
            "name": draft.name,
            "description": draft.description,
            "enabled": True,
            "capabilities": [c.model_dump(exclude_none=True) for c in draft.capabilities],
            "adapter": {"kind": "declared", "spec": {"source_description": draft.description}},
            "runtimes": [_state_runtime("preparing")],
            "active_runtime": "preparing",
            "source": {"kind": row.target_kind, "target": row.target},
            "icon": {"kind": "letter", "text": draft.name[:1].upper()},
            "origin": "connected",
        },
    )
    row.provider_id = provider.id
    row.state = "connected"
    db.commit()
    db.refresh(provider)
    log.info("bridge: %s is waiting for a connection to be built", provider.slug)
    return provider


def rebuild(db: Session, provider: Provider) -> str:
    """Look at the project again, and build a connection only if it still needs one.

    Returns a plain sentence for the person.
    """
    from app.services import connect as connect_service

    if not (provider.source or {}).get("target"):
        raise BridgeError("Bevro doesn't know where this was connected from.")
    try:
        connect_service.reconnect_provider(db, provider)
    except connect_service.DraftError as exc:
        log.info("bridge: nothing native found for %s any more (%s)", provider.slug, exc)
    native = [rt for rt in runtime_service.runtimes_of(provider) if rt.invocable and rt.id != BRIDGE_RUNTIME_ID]
    if native:
        # The project grew its own way in: prefer it and leave the bridge in place.
        runtimes = runtime_service.runtimes_of(provider)
        runtime_service.set_runtimes(provider, runtimes, _preferred_id(runtimes))
        db.commit()
        return f"This project now has its own connection, which Bevro will use: {runtime_service.active_runtime(provider).display_name.lower()}."
    if builder_for(db) is None:
        raise BridgeError(NO_BUILDER_MESSAGE, 503)
    # Looking again happens where the project is. If it turns out to have its
    # own way in by then, that is used and nothing is built.
    _set_state(db, provider, "preparing")
    return "Bevro is looking at the project again. If it has no connection of its own, a new one will be built; this takes a few minutes."


# --------------------------------------------------------------------------- what the worker does

def pending_providers(db: Session) -> list[Provider]:
    return [p for p in provider_service.list_providers(db) if build_state(p) is not None]


def run_pending(db: Session, roots: list[Path]) -> int:
    """Worker entry point: carry each waiting connection to its next step.

    Everything here needs the project itself, which is why it runs on the
    machine that has it.
    """
    if not roots:
        return 0
    handled = 0
    for provider in pending_providers(db):
        state = build_state(provider)
        try:
            if state == "preparing":
                _prepare_and_submit(db, provider)
                handled += 1
            elif state in ("building", "testing"):
                handled += 1 if _follow_build(db, provider) else 0
        except BridgeError as exc:
            if not exc.recorded:
                _record_failure(db, provider, str(exc), message=str(exc))
        except Exception:  # noqa: BLE001 - one bad project must not stop the worker
            log.exception("bridge: building a connection for %s crashed", provider.slug)
            _record_failure(db, provider, "something went wrong while building the connection")
    return handled


def _draft_for_source(provider: Provider) -> ProviderDraft:
    """Look at the project the provider was connected from, as Connect would."""
    from app.connect.service import get_discovery_service
    from app.connect.strategies.base import DiscoveryContext
    from app.connect.targets import stored_target
    from app.services.connect import local_roots

    source = provider.source or {}
    target = stored_target(str(source.get("target_kind") or source.get("kind") or ""), str(source.get("target") or ""))
    return get_discovery_service().discover(target, DiscoveryContext(roots=local_roots()))


def _prepare_and_submit(db: Session, provider: Provider) -> Task | None:
    """Read the project, and either adopt the way in it now has or ask for one.

    This runs where the project is, which is why the "has it grown its own
    interface?" question is answered here rather than in the API.
    """
    builder = builder_for(db)
    if builder is None:
        raise BridgeError(NO_BUILDER_MESSAGE)
    draft = _draft_for_source(provider)
    if any(rt.invocable for rt in draft.runtimes):
        _adopt_native(db, provider, draft)
        return None
    spec, project_path = spec_for(db, provider, draft)
    folder = integration_dir(provider.id)
    folder.mkdir(parents=True, exist_ok=True)
    # Kept where the builder cannot reach it, so a changed project cannot be hidden.
    _store_snapshot(provider.id, snapshot(Path(project_path)))
    workspace = workspace_service.create_internal(
        db,
        slug=f"bridge-{provider.id}",
        name=f"Connection for {provider.name}",
        path=workspace_reference(provider.id),
        read_paths=[project_path],
        description="Bevro's own folder for this connection",
    )
    task = task_service.submit(
        db,
        build_prompt(spec),
        provider=builder,
        input={"workspace_id": str(workspace.id)},
        trusted=True,
        title=f"Preparing a connection for {provider.name}",
    )
    task.routing = {**(task.routing or {}), "bridge": {"provider_id": str(provider.id), "builder": builder.slug}}
    _set_state(db, provider, "building")
    db.commit()
    log.info("bridge: %s is building a connection for %s (task %s)", builder.slug, provider.slug, task.id)
    return task


def _adopt_native(db: Session, provider: Provider, draft: ProviderDraft) -> None:
    """The project has a way in of its own now: prefer it, keep any built
    connection as a fallback."""
    built = [rt for rt in runtime_service.runtimes_of(provider) if (rt.adapter.get("config") or {}).get("bridge")]
    runtimes = [*draft.runtimes, *(rt for rt in built if all(rt.id != other.id for other in draft.runtimes))]
    runtime_service.set_runtimes(provider, runtimes, _preferred_id(runtimes))
    provider.availability = None
    db.commit()
    log.info("bridge: %s now has its own way in (%s); no connection was built", provider.slug, runtime_service.active_runtime(provider).kind.value)


def _follow_build(db: Session, provider: Provider) -> bool:
    """Once the building task is done, check what came back and register it."""
    task = build_task(db, provider)
    if task is None:
        _set_state(db, provider, "preparing")  # lost the task; start again
        return True
    if task.state not in ("completed", "failed", "cancelled"):
        return False
    if task.state != "completed":
        _record_failure(db, provider, f"the building task {task.state}")
        return True
    _set_state(db, provider, "testing")
    builder_slug = ((task.routing or {}).get("bridge") or {}).get("builder") or "a coding agent"
    validate_and_register(db, provider, source_before=_load_snapshot(provider.id), builder_slug=builder_slug)
    return True


def build_task(db: Session, provider: Provider) -> Task | None:
    """The most recent task that was asked to build this provider's connection."""
    from sqlalchemy import select

    stmt = (
        select(Task)
        .where(Task.routing["bridge"]["provider_id"].astext == str(provider.id))
        .order_by(Task.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _snapshot_path(provider_id: uuid.UUID | str) -> Path:
    # Beside the integration folders, not inside one: a builder is given its
    # own folder and nothing else, so it cannot touch this.
    root = Path(get_settings().integrations_dir)
    return root / ".snapshots" / f"{provider_id}.json"


def _store_snapshot(provider_id: uuid.UUID | str, data: dict[str, str]) -> None:
    path = _snapshot_path(provider_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _load_snapshot(provider_id: uuid.UUID | str) -> dict[str, str] | None:
    try:
        return json.loads(_snapshot_path(provider_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def spec_for(db: Session, provider: Provider, draft: ProviderDraft) -> tuple[BridgeSpec, str]:
    """The BridgeSpec, and the approved project path it names."""
    from app.services.connect import local_roots

    target = str((provider.source or {}).get("target") or "")
    try:
        path = resolve_within(target, local_roots())
    except OutsideRoots as exc:
        raise BridgeError(f"Bevro hasn't been allowed to work in that folder: {exc}") from None
    project = Project(path)
    findings = [f for f in (inspect_python(project), inspect_node(project), inspect_docker(project), inspect_scripts(project)) if f is not None]
    _title, prose = readme_excerpt(project)
    spec = build_spec(
        provider_id=str(provider.id),
        provider_name=provider.name,
        description=provider.description or draft.description,
        project_path=str(path),
        bridge_dir=str(integration_dir(provider.id)),
        project=project,
        findings=findings,
        capabilities=[c.get("id") for c in provider.capabilities if isinstance(c, dict) and c.get("id")],
        readme_excerpt=prose,
    )
    return spec, str(path)


# --------------------------------------------------------------------------- validating what came back

VALIDATION_FAILED = "Bevro couldn't create a reliable connection for this project."


def validate_and_register(db: Session, provider: Provider, *, spec: BridgeSpec | None = None, source_before: dict[str, str] | None = None, builder_slug: str = "a coding agent") -> RuntimeProfile:
    """Check the bridge, prove the project is untouched, then register the runtime.

    Raises BridgeError with a plain message if anything is wrong; no broken
    runtime is ever left active.
    """
    if spec is None:
        draft = _draft_for_source(provider)
        spec, _path = spec_for(db, provider, draft)
    folder = integration_dir(provider.id)
    project_path = Path(spec.project_path)

    # 1. The project must be exactly as it was.
    if source_before is not None:
        changes = changed_files(source_before, snapshot(project_path))
        if any(changes.values()):
            log.warning("bridge: the project %s was modified while building a connection: %s", provider.slug, changes)
            message = "Bevro stopped: building the connection would have changed your project, so nothing was connected."
            _record_failure(db, provider, "the project was changed while building the connection", changes, message)
            raise BridgeError(message, recorded=True)

    # 2. There must be a bridge with a manifest.
    manifest = read_manifest(folder)
    if manifest is None or not (folder / bridge_module.BRIDGE_FILE).is_file():
        _record_failure(db, provider, "no bridge was produced")
        raise BridgeError(VALIDATION_FAILED, recorded=True)
    command = [str(c) for c in manifest.get("command") or []]
    if not command:
        _record_failure(db, provider, "the bridge did not say how to run it")
        raise BridgeError(VALIDATION_FAILED, recorded=True)

    # 3. It must answer a sample request with the contract Bevro expects.
    runtime = runtime_from_manifest({**manifest, "built_by": manifest.get("built_by") or builder_slug})
    result = probe(provider, runtime)
    if not result[0]:
        _record_failure(db, provider, result[1])
        raise BridgeError(VALIDATION_FAILED, recorded=True)

    # 4. Keep Bevro's own record, and register it as an ordinary runtime.
    write_manifest(
        folder,
        spec,
        command=command,
        builder=manifest.get("built_by") or builder_slug,
        callable_used=manifest.get("callable"),
        fingerprint_value=fingerprint(project_path),
        validation="passed",
        notes=manifest.get("notes"),
    )
    runtime = runtime_from_manifest(read_manifest(folder) or manifest)
    existing = [rt for rt in runtime_service.runtimes_of(provider) if rt.id not in STATES and rt.id != BRIDGE_RUNTIME_ID]
    runtime_service.set_runtimes(provider, [*existing, runtime], _preferred_id([*existing, runtime]))
    provider.availability = None
    db.commit()
    log.info("bridge: connection for %s validated and registered", provider.slug)
    return runtime


def _preferred_id(runtimes: list[RuntimeProfile]) -> str | None:
    from app.connect.runtimes import select

    chosen, _choice = select(runtimes)
    return chosen


def probe(provider: Provider, runtime: RuntimeProfile) -> tuple[bool, str]:
    """Run one sample request through the bridge. (ok, what went wrong)."""
    from adapters import InvocationRequest

    spec_provider = runtime_service.spec_for(provider, runtime)
    adapter = runtime_service.adapter_for(runtime)
    request = InvocationRequest(
        task_id="bridge-check",
        run_id="bridge-check",
        request=SAMPLE_REQUEST,
        input={"integration_dir": str(integration_dir(provider.id))},
    )
    try:
        result = adapter.invoke(spec_provider, request)
    except Exception as exc:  # noqa: BLE001
        return False, f"the bridge raised {type(exc).__name__}"
    if result.state.value not in ("completed", "needs_input"):
        return False, f"the bridge answered {result.state.value}: {result.error or 'no reason given'}"
    if not result.summary:
        return False, "the bridge answered without a summary"
    return True, ""


def _record_failure(db: Session, provider: Provider, reason: str, evidence: dict[str, Any] | None = None, message: str | None = None) -> None:
    """Keep what went wrong server-side; leave nothing usable behind."""
    folder = integration_dir(provider.id)
    message = message or VALIDATION_FAILED
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "validation-failed.json").write_text(json.dumps({"reason": reason, "message": message, "evidence": evidence or {}}, indent=2), encoding="utf-8")
    except OSError:
        pass
    log.warning("bridge: %s was not connected: %s", provider.slug, reason)
    keep = [rt for rt in runtime_service.runtimes_of(provider) if rt.id not in STATES and rt.id != BRIDGE_RUNTIME_ID]
    runtimes = keep or [_needs_attention_runtime(message)]
    runtime_service.set_runtimes(provider, runtimes, _preferred_id(runtimes))
    db.commit()


def _needs_attention_runtime(message: str) -> RuntimeProfile:
    return RuntimeProfile(
        id="unbuilt",
        kind=RuntimeKind.UNKNOWN,
        display_name="No connection yet",
        confidence="low",
        availability="not_invocable",
        adapter={},
        evidence=[],
        warnings=[message],
    )


# --------------------------------------------------------------------------- what the browser sees

def status(db: Session, provider: Provider) -> dict[str, Any]:
    """Plain words about where a connection has got to. No mechanism, no logs."""
    state = build_state(provider)
    if state is None:
        runtime = runtime_service.active_runtime(provider)
        ready = runtime is not None and runtime.invocable
        return {
            "state": "ready" if ready else "failed",
            "note": (runtime.display_name if ready else (runtime.warnings[0] if runtime and runtime.warnings else VALIDATION_FAILED)),
            "provider_id": str(provider.id),
            "task_id": None,
            "steps": BUILD_STEPS if ready else [],
        }
    task = build_task(db, provider)
    return {
        "state": state,
        "note": STATES[state],
        "provider_id": str(provider.id),
        "task_id": str(task.id) if task is not None else None,
        "steps": BUILD_STEPS[: BUILD_STATE_IDS.index(state) + 1],
    }


BUILD_STEPS = ["Inspecting project", "Building connection", "Testing connection"]


# --------------------------------------------------------------------------- keeping a bridge honest

def bridge_runtime(provider: Provider) -> RuntimeProfile | None:
    return next((rt for rt in runtime_service.runtimes_of(provider) if rt.id == BRIDGE_RUNTIME_ID), None)


def review_state(provider: Provider) -> str | None:
    """"needs_review" when the project has moved on since the bridge was built."""
    runtime = bridge_runtime(provider)
    if runtime is None:
        return None
    recorded = ((runtime.adapter.get("config") or {}).get("bridge") or {}).get("fingerprint")
    if not recorded:
        return None
    target = str((provider.source or {}).get("target") or "")
    try:
        from app.services.connect import local_roots

        path = resolve_within(target, local_roots())
    except (OutsideRoots, ValueError):
        return None
    return "needs_review" if fingerprint(path) != recorded else None
