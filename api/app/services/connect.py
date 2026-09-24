"""Connect, from typed target to connected provider.

    start_discovery()   classify, then discover now (URLs, MCP over HTTP; local
                        things when this process has approved roots) or leave
                        a pending draft for the worker on the host
    run_pending()       what the worker calls: discover / test pending drafts
    test_draft()        a safe probe of a found draft, before anything is saved
    confirm_draft()     turn a draft into a Provider (+ encrypted secrets)

Local paths and commands are only ever resolved on the machine that holds
them, inside BEVRO_LOCAL_ROOTS. The API in Docker never sees them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters import ProviderSpec, get_adapter
from adapters.localroots import parse_roots
from adapters.runtime import CredentialStrategy
from app.config import get_settings
from app.connect.capabilities import capabilities_from_summary
from app.connect.draft import ProviderDraft
from app.connect.service import get_discovery_service
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed
from app.connect.strategies.mcp import refine_stdio
from app.connect.targets import TargetError, classify_target
from app.models import ConnectDraft, Provider
from app.models._common import utcnow
from app.services import providers as provider_service
from app.services.secrets import SecretStore

log = logging.getLogger("bevro.connect")

PENDING_TIMEOUT_SECONDS = 90
WORKER_NEEDED_MESSAGE = (
    "Connecting a local project or command needs the Bevro worker running on this machine "
    "(./scripts/worker.sh), with BEVRO_LOCAL_ROOTS set to the folder that holds your agents."
)
LOCAL_KINDS = ("local", "command")


class DraftError(Exception):
    """A message for the person; the HTTP layer picks the status."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def local_roots() -> list[Path]:
    return parse_roots(get_settings().local_roots)


def can_discover_locally() -> bool:
    return bool(local_roots())


def is_local(draft: ConnectDraft) -> bool:
    return draft.target_kind in LOCAL_KINDS


def needs_host(pd: ProviderDraft) -> bool:
    """Drafts whose adapter runs on the host: commands, stdio MCP servers."""
    config = pd.adapter.get("config") or {}
    return pd.adapter.get("kind") == "command" or (pd.adapter.get("kind") == "mcp" and bool(config.get("argv")))


# --------------------------------------------------------------------------- discovery

def start_discovery(db: Session, target_text: str, secrets: dict[str, str] | None = None) -> ConnectDraft:
    try:
        target = classify_target(target_text)
    except TargetError as exc:
        raise DraftError(str(exc)) from exc
    row = ConnectDraft(target_kind=target.kind, target=target.value, state="pending")
    db.add(row)
    db.flush()
    # Folders are only readable on the host, so they go to the worker unless this
    # process has roots of its own. A command is classified from its text alone.
    if target.kind == "local" and not can_discover_locally():
        if provider_service.worker_seen_recently(db):
            row.state = "pending"  # the worker will pick it up
        else:
            row.state = "failed"
            row.error = WORKER_NEEDED_MESSAGE
    else:
        _discover_into(row, local_roots(), secrets or {})
    db.commit()
    db.refresh(row)
    return row


def _discover_into(row: ConnectDraft, roots: list[Path], secrets: dict[str, str]) -> None:
    try:
        target = classify_target(row.target)
        draft = get_discovery_service().discover(target, DiscoveryContext(roots=roots, secrets=secrets))
    except (DiscoveryFailed, TargetError) as exc:
        row.state = "failed"
        row.error = str(exc)
        row.draft = None
        return
    except Exception:  # noqa: BLE001 - discovery must never take the process down
        log.exception("discovery crashed for a %s target", row.target_kind)
        row.state = "failed"
        row.error = "Something went wrong while looking at that. The server log has the details."
        return
    row.state = "found"
    row.error = None
    row.draft = draft.model_dump()


def get_draft(db: Session, draft_id: uuid.UUID) -> ConnectDraft | None:
    row = db.get(ConnectDraft, draft_id)
    if row is None:
        return None
    if row.state in ("pending", "testing") and (utcnow() - row.updated_at) > timedelta(seconds=PENDING_TIMEOUT_SECONDS):
        if row.state == "pending":
            row.state = "failed"
            row.error = "The worker didn't respond. Is ./scripts/worker.sh running on this machine?"
        else:
            row.state = "found"
            row.test = {"ok": False, "detail": "The worker didn't respond to the test."}
        db.commit()
    return row


def run_pending(db: Session, roots: list[Path]) -> int:
    """Worker entry point: discover pending drafts, test drafts marked for testing."""
    from sqlalchemy import text

    rows = db.execute(
        text("SELECT id FROM connect_drafts WHERE state IN ('pending', 'testing') AND target_kind IN ('local', 'command') ORDER BY created_at LIMIT 5 FOR UPDATE SKIP LOCKED")
    ).all()  # commands appear here only when a draft is being tested on the host
    handled = 0
    for (draft_id,) in rows:
        row = db.get(ConnectDraft, draft_id)
        if row is None:
            continue
        if row.state == "pending":
            _discover_into(row, roots, {})
        elif row.state == "testing":
            _test_on_host(row, roots)
        handled += 1
    db.commit()
    return handled


# --------------------------------------------------------------------------- test

def test_draft(db: Session, row: ConnectDraft, secrets: dict[str, str]) -> ConnectDraft:
    if row.state not in ("found",) or not row.draft:
        raise DraftError("There is nothing to test yet.", 409)
    pd = ProviderDraft.model_validate(row.draft)
    if needs_host(pd) and not can_discover_locally():
        if not provider_service.worker_seen_recently(db):
            raise DraftError(WORKER_NEEDED_MESSAGE, 409)
        row.state = "testing"
        row.test = None
        db.commit()
        db.refresh(row)
        return row
    if needs_host(pd):
        _test_on_host(row, local_roots())
    else:
        row.test = _test_remote(pd, secrets)
        if row.test.get("draft"):
            row.draft = row.test.pop("draft")
    db.commit()
    db.refresh(row)
    return row


def _test_remote(pd: ProviderDraft, secrets: dict[str, str]) -> dict[str, Any]:
    """HTTP and MCP-over-HTTP: a health request or an MCP initialize, in this process."""
    kind = str(pd.adapter.get("kind") or "")
    if pd.availability == "needs_start" and kind == "http":
        # A project that runs as a web service: now that it may be running, read what it publishes.
        base = str((pd.adapter.get("config") or {}).get("base_url") or "")
        try:
            target = classify_target(base)
            found = get_discovery_service().discover(target, DiscoveryContext(secrets=secrets))
        except (DiscoveryFailed, TargetError) as exc:
            return {"ok": False, "detail": str(exc)}
        merged = found.model_copy(update={"name": pd.name if pd.name else found.name, "description": pd.description or found.description, "capabilities": found.capabilities or pd.capabilities, "evidence": [*pd.evidence, *found.evidence]})
        return {"ok": True, "detail": "Reached it and read its description.", "draft": merged.model_dump()}
    try:
        adapter = get_adapter(kind)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}
    spec = ProviderSpec(id="draft", slug="draft", name=pd.name, capabilities=[c.model_dump() for c in pd.capabilities], adapter=pd.adapter)
    result = adapter.check(spec, secrets)
    return {"ok": result.ok, "detail": result.detail or ("Reachable." if result.ok else "Not reachable.")}


def _test_on_host(row: ConnectDraft, roots: list[Path]) -> None:
    """Commands and stdio MCP servers: validate the folder and the program on the host.
    A local MCP server is started once to read its tools (the person asked for the test)."""
    pd = ProviderDraft.model_validate(row.draft or {})
    kind = str(pd.adapter.get("kind") or "")
    row.state = "found"
    try:
        adapter = get_adapter(kind)
        spec = ProviderSpec(id="draft", slug="draft", name=pd.name, adapter=pd.adapter)
        if kind == "mcp":
            refined = refine_stdio(pd, {})
            row.draft = refined.model_dump()
            row.test = {"ok": True, "detail": f"Started it and read {len(refined.capabilities)} tool(s)."}
            return
        result = adapter.check(spec, {})
        row.test = {"ok": result.ok, "detail": result.detail or ("The folder and the program are in place." if result.ok else "Not ready.")}
    except DiscoveryFailed as exc:
        row.test = {"ok": False, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("test on host failed")
        row.test = {"ok": False, "detail": f"The test failed: {type(exc).__name__}"}


# --------------------------------------------------------------------------- confirm

def _scoped_to(pd: ProviderDraft, chosen: str) -> ProviderDraft:
    """Fix this connection to one profile, workspace or tenant.

    The name of the parameter comes from the service's own operations, so
    nothing here knows what kind of thing is being chosen.
    """
    from app.connect.openapi import catalogue_from_json

    config = pd.adapter.get("config") or {}
    catalogue = catalogue_from_json(config.get("operations"))
    parameter = next((n for op in catalogue for n in op.scope_parameters()), None)
    if parameter is None:
        return pd
    context = {**(config.get("context") or {}), parameter: chosen}
    pd.adapter = {**pd.adapter, "config": {**config, "context": context}}
    pd.source = {**(pd.source or {}), "connection_context": context}
    pd.scope_choices = []
    pd.invocable = True
    for rt in pd.runtimes:
        if str(rt.adapter.get("kind")) == "openapi":
            rt.adapter = {**rt.adapter, "config": {**(rt.adapter.get("config") or {}), "context": context}}
            rt.availability = "ready"
    return pd


def confirm_draft(db: Session, row: ConnectDraft, *, name: str | None, description: str | None, capability_summary: str | None, secrets: dict[str, str], app_url: str | None, enabled: bool = True, runtime_id: str | None = None, scope: str | None = None) -> Provider:
    if row.state == "connected":
        raise DraftError("This has already been connected.", 409)
    if row.state != "found" or not row.draft:
        raise DraftError("Nothing has been found to connect yet.", 409)
    pd = ProviderDraft.model_validate(row.draft)
    if runtime_id:
        if not any(rt.id == runtime_id and rt.invocable for rt in pd.runtimes):
            raise DraftError("That isn't one of the ways Bevro found.", 422)
        pd.with_runtimes(pd.runtimes, runtime_id, False)
    elif pd.choice_needed:
        raise DraftError("Choose how Bevro should connect to it first.", 422)
    if pd.scope_choices:
        if not scope:
            raise DraftError("Choose which one this connection is for first.", 422)
        if scope not in [c.get("value") for c in pd.scope_choices]:
            raise DraftError("That isn't one of the ones Bevro found.", 422)
        pd = _scoped_to(pd, scope)
    if not pd.adapter.get("kind"):
        raise DraftError("Bevro found this, but has no way to run it. Use Advanced setup.", 409)
    capabilities = [c.model_dump(exclude_none=True) for c in pd.capabilities]
    if capability_summary is not None:
        capabilities = [c.model_dump(exclude_none=True) for c in capabilities_from_summary(capability_summary)]
    store = None
    values = {k: v for k, v in secrets.items() if v}
    if values:
        try:
            store = SecretStore()
        except RuntimeError as exc:
            raise DraftError("Secrets cannot be stored until the server has a secret key.", 503) from exc
    adapter = dict(pd.adapter)
    config = dict(adapter.get("config") or {})
    # A typed credential decides how the adapter authenticates.
    if pd.auth.secret_name and pd.auth.secret_name in values:
        if adapter.get("kind") in ("http", "mcp") and not config.get("argv"):
            config.setdefault("auth", {"type": "bearer", "secret": pd.auth.secret_name})
        elif adapter.get("kind") in ("command", "mcp"):
            names = list(config.get("secret_env") or [])
            if pd.auth.secret_name not in names:
                names.append(pd.auth.secret_name)
            config["secret_env"] = names
    adapter["config"] = config
    adapter["method"] = {"http": "api", "mcp": "mcp", "command": "command"}.get(str(adapter.get("kind")), str(adapter.get("kind")))
    runtimes = list(pd.runtimes)
    active = pd.runtime
    if active is not None:
        active.adapter = dict(adapter)  # the credential decision above belongs to the active profile
        if pd.auth.secret_name and pd.auth.secret_name in values:
            active.credentials = active.credentials.model_copy(update={"strategy": CredentialStrategy.BEVRO_MANAGED, "required_from_user": False, "note": "Stored encrypted by Bevro."})
    final_name = (name or pd.name).strip()[:120]
    provider = provider_service.register_provider(
        db,
        {
            "name": final_name,
            "description": (description if description is not None else pd.description).strip()[:2000],
            "enabled": enabled,
            "capabilities": capabilities,
            "adapter": adapter,
            "runtimes": runtimes,
            "active_runtime": active.id if active else None,
            "source": {"kind": row.target_kind, "target": row.target, **(pd.source or {}), "validated_at": utcnow().isoformat()},
            "app_url": (app_url or pd.app_url) or None,
            "icon": {"kind": "letter", "text": final_name[:1].upper()},
            "origin": "connected",
        },
    )
    if store is not None:
        for secret_name, value in values.items():
            store.put(db, provider.id, secret_name[:80], value)
    row.state = "connected"
    row.provider_id = provider.id
    db.commit()
    db.refresh(provider)
    return provider


# --------------------------------------------------------------------------- reconnect

def reconnect_provider(db: Session, provider: Provider) -> Provider:
    """Discover the stored target again and replace the runtime profiles. Local
    targets need this process to have roots (or the worker); URLs work anywhere."""
    from app.services.runtime import set_runtimes

    source = provider.source or {}
    target_text = str(source.get("target") or "")
    if not target_text:
        raise DraftError("Bevro doesn't know where this was connected from. Connect it again instead.", 409)
    try:
        target = classify_target(target_text)
    except TargetError as exc:
        raise DraftError(str(exc)) from exc
    if target.kind in LOCAL_KINDS and target.kind == "local" and not can_discover_locally():
        raise DraftError("Reconnecting a local project needs the API to see the folder. Use Test under Manage, or connect it again.", 409)
    try:
        draft = get_discovery_service().discover(target, DiscoveryContext(roots=local_roots()))
    except (DiscoveryFailed, TargetError) as exc:
        raise DraftError(str(exc), 409) from exc
    if not draft.runtimes:
        raise DraftError("Nothing usable was found there any more.", 409)
    stored = SecretStore().names(db, provider.id) if _has_secret_store() else []
    active = draft.runtime
    if active is not None and active.credentials.names and all(n in stored for n in active.credentials.names):
        active.credentials = active.credentials.model_copy(update={"strategy": CredentialStrategy.BEVRO_MANAGED, "required_from_user": False})
    # A connection Bevro built for this project is not something discovery can
    # find again, so it is kept; ranking decides whether it is still preferred.
    from app.services.runtime import runtimes_of

    built = [rt for rt in runtimes_of(provider) if (rt.adapter.get("config") or {}).get("bridge")]
    runtimes = [*draft.runtimes, *(rt for rt in built if all(rt.id != other.id for other in draft.runtimes))]
    active_id = draft.active_runtime
    if len(runtimes) > len(draft.runtimes):
        from app.connect.runtimes import select

        active_id, _choice = select(runtimes)
    set_runtimes(provider, runtimes, active_id)
    provider.availability = None  # the worker reports afresh
    db.commit()
    db.refresh(provider)
    return provider


def _has_secret_store() -> bool:
    try:
        SecretStore()
    except RuntimeError:
        return False
    return True


# --------------------------------------------------------------------------- serialise

def target_label(row: ConnectDraft) -> str:
    if row.target_kind == "local":
        name = row.target.rstrip("/").rsplit("/", 1)[-1]
        return name or "folder"
    return row.target


def draft_public(row: ConnectDraft, db: Session | None = None) -> dict[str, Any] | None:
    if not row.draft:
        return None
    public = ProviderDraft.model_validate(row.draft).public()
    if public.get("needs_bridge") and db is not None:
        from app.services import bridges as bridge_service

        # Only offer to build one when something can actually build it.
        public["bridge_possible"] = bridge_service.bridge_possible(db)
    return public


def list_recent(db: Session, limit: int = 20) -> list[ConnectDraft]:
    return list(db.scalars(select(ConnectDraft).order_by(ConnectDraft.created_at.desc()).limit(limit)))
