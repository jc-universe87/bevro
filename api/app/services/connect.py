"""Connect, from typed target to connected provider.

    start_discovery()   classify, then discover now (URLs, MCP over HTTP; local
                        things when this process has approved roots) or leave
                        a pending draft for the worker on the host
    run_pending()       what the worker calls: discover / test pending drafts
    test_draft()        a safe probe of a found draft, before anything is saved
    confirm_draft()     turn a draft into a Provider (+ encrypted secrets)

Local paths and commands are only ever resolved on the machine that holds
them, and only where the person has allowed it. The API in Docker never
sees them.
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
from adapters.runtime import API, WORKER, CredentialStrategy
from app.config import get_settings
from app.connect.capabilities import capabilities_from_summary
from app.connect.draft import ProviderDraft
from app.connect.service import get_discovery_service
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed, NotReachable
from app.connect.strategies.mcp import refine_stdio
from app.connect.targets import ConnectTarget, TargetError, classify_target, stored_target
from app.models import ConnectDraft, Provider
from app.models._common import utcnow
from app.services import providers as provider_service
from app.services import reconcile as reconcile_service
from app.services import trust as trust_service
from app.services.secrets import SecretStore

log = logging.getLogger("bevro.connect")

PENDING_TIMEOUT_SECONDS = 90
WORKER_NEEDED_MESSAGE = (
    "Connecting something on this machine needs the Bevro worker running here "
    "(./scripts/worker.sh). Nothing else has to be set up."
)
UNREACHABLE_HERE_MESSAGE = (
    "Bevro can't reach that address from inside its container, and the worker isn't running on this "
    "machine to try from here (./scripts/worker.sh)."
)
LOCAL_KINDS = ("local", "command", "name")
NOT_FOUND_BY_NAME = "Bevro couldn't find anything called “{name}” on this machine. If you know where it is, paste the folder's full path instead."


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


def api_ruled_out(pd: ProviderDraft) -> bool:
    """An address this process has already tried and could not reach.

    Nothing is wrong with the service; it is simply on a network the
    container is not. Testing it from here would only fail again.
    """
    rt = pd.runtime
    return rt is not None and rt.reachability.ruled_out(API)


# --------------------------------------------------------------------------- discovery

def start_discovery(db: Session, target_text: str, secrets: dict[str, str] | None = None) -> ConnectDraft:
    try:
        target = classify_target(target_text)
    except TargetError as exc:
        raise DraftError(str(exc)) from exc
    row = ConnectDraft(target_kind=target.kind, target=target.value, state="pending")
    db.add(row)
    db.flush()
    # Anything on this machine - a folder, a program - is the worker's to look
    # at. Whether it exists, where it really is once symlinks are followed,
    # and whether the person has agreed to it are all host facts, and the API
    # inside a container has no business guessing at any of them.
    if target.kind in LOCAL_KINDS and not can_discover_locally():
        if provider_service.worker_seen_recently(db):
            row.state = "pending"  # the worker will look, and ask if it has to
        else:
            row.state = "failed"
            row.error = WORKER_NEEDED_MESSAGE
    else:
        _discover_into(row, local_roots(), secrets or {}, db=db)
        # An address this process cannot reach may still be reachable from the
        # host, which sits on networks a container does not. The worker looks
        # next; to the person it is all one "Looking…".
        if row.state == "failed" and row.unreachable and provider_service.worker_seen_recently(db):
            log.info("%s is not reachable from here; the worker will look", row.target_kind)
            row.state = "pending"
            row.error = None
    db.commit()
    db.refresh(row)
    return row


def _discover_into(row: ConnectDraft, roots: list[Path], secrets: dict[str, str], *, location: str = API, db: Session | None = None) -> None:
    # The API has already tried, and failed, if this draft was handed on.
    ruled_out = API if row.unreachable and location != API else None
    try:
        target = target_of(row)
        # Something on this machine is not looked at until the person has said
        # it may be. The question is asked once, about that one thing.
        if db is not None and target.kind in LOCAL_KINDS:
            # A name is first turned into the folder it names - or into a
            # question, when it could mean more than one. Only then is there a
            # "that one thing" to ask about.
            if target.kind == "name":
                if _resolve_name(db, row, target):
                    return
                target = target_of(row)
            if _ask_first(db, row, target):
                return
            roots = trust_service.apply_to_process(db)
        draft = get_discovery_service().discover(target, DiscoveryContext(roots=roots, secrets=secrets))
    except NotReachable as exc:
        # Not "it is not there" - "it is not there *from here*".
        row.state = "failed"
        row.error = str(exc)
        row.draft = None
        row.unreachable = True
        return
    except (DiscoveryFailed, TargetError) as exc:
        row.state = "failed"
        row.error = str(exc)
        row.draft = None
        row.unreachable = False
        return
    except Exception:  # noqa: BLE001 - discovery must never take the process down
        log.exception("discovery crashed for a %s target", row.target_kind)
        row.state = "failed"
        row.error = "Something went wrong while looking at that. The server log has the details."
        return
    # Whoever found it can reach it; that is recorded so execution knows -
    # and so is whoever already tried and could not, so the first run does
    # not repeat an attempt that is known to time out.
    for rt in draft.runtimes:
        rt.reachability = rt.reachability.with_result(location, True)
        if ruled_out:
            rt.reachability = rt.reachability.with_result(ruled_out, False)
    row.state = "found"
    row.error = None
    row.unreachable = False
    row.draft = draft.settled().model_dump(mode="json")


def _ask_first(db: Session, row: ConnectDraft, target: ConnectTarget) -> bool:
    """Does this need permission before anything is looked at or run?

    True when the draft now holds a question rather than a result. What is
    put in front of the person is the *resolved* path - where the thing
    actually is once symlinks are followed - so that what they agree to is
    what they will get.
    """
    facts = trust_service.look_at_command(target.argv) if target.kind == "command" else trust_service.look_at(_where(db, target))
    if not facts.get("exists"):
        row.state = "failed"
        row.error = facts.get("reason") or "Bevro couldn't find that on this machine."
        row.draft = None
        return True
    if facts.get("reason"):
        row.state = "failed"
        row.error = facts["reason"]
        row.draft = None
        return True
    granted = trust_service.allows_command(db, target.argv) if target.kind == "command" else trust_service.allows_folder(db, facts["path"])
    if granted:
        return False
    row.state = "trust_required"
    row.trust = facts
    row.error = None
    row.draft = None
    log.info("%s needs permission before Bevro looks at it", target.kind)
    return True


def _where(db: Session, target: ConnectTarget) -> str:
    """The path a local target means, including a bare name under a folder
    the person has already approved."""
    from adapters.localroots import find_by_name

    if target.value.startswith(("/", "~")):
        return target.value
    found = find_by_name(target.value, trust_service.folder_roots(db))
    return str(found) if found else target.value


def target_of(row: ConnectDraft) -> ConnectTarget:
    """The draft's target as it now stands: what was typed, or - once the
    worker has said - what a name turned out to be."""
    return stored_target(row.target_kind, row.target)


def _resolve_name(db: Session, row: ConnectDraft, target: ConnectTarget) -> bool:
    """Turn what something is called into where it is. On the worker only.

    True when the draft now holds a question or a failure rather than a
    folder. Nothing is granted and nothing inside any folder is read: the
    folder found goes on to the same permission question a typed path would.
    """
    from app.connect import names

    if "maybe_command" in target.hints and trust_service.look_at_command(target.argv).get("exists"):
        # A program on this machine's PATH, followed by its arguments: what
        # it always was.
        row.target_kind = "command"
        return False
    paths = names.find_named(target.value, search_areas(db), refuse=trust_service.refuse_reason)
    row.named = target.value[:200]
    row.draft = None
    if not paths:
        row.state = "failed"
        row.error = NOT_FOUND_BY_NAME.format(name=target.value)
        return True
    if len(paths) > 1:
        # Guessing between two folders is guessing which project the person
        # meant. They are asked; nothing is looked at until they say.
        row.state = "choice_required"
        row.error = None
        row.candidates = [{"path": str(p), "label": p.name, "where": names.shown(p)} for p in paths]
        log.info("a name fits %d folders; asking which", len(paths))
        return True
    row.target_kind = "local"
    row.target = str(paths[0])
    return False


def search_areas(db: Session) -> list:
    """Where a name is looked for, most likely first.

        folders the person already allowed       and what is just below them
        folders their connected projects sit in  a project's neighbours
        the administrator's roots, if any        which are also the boundary
        this user's own home                     only where there is no boundary

    Each to a fixed depth. Nothing outside these is looked at, and nothing
    in them that could never be allowed is entered.
    """
    import os

    from app.connect.names import SearchArea

    areas: list[SearchArea] = [SearchArea(Path(row.target), 2) for row in trust_service.active_grants(db, trust_service.FOLDER)]
    for provider in db.scalars(select(Provider).where(Provider.origin == "connected")):
        source = provider.source or {}
        where = str(source.get("target") or "")
        if str(source.get("target_kind") or source.get("kind") or "") == "local" and where.startswith(("/", "~")):
            areas.append(SearchArea(Path(os.path.expanduser(where)).parent, 2))
    ceiling = trust_service.admin_ceiling()
    areas += [SearchArea(root, 3) for root in ceiling]
    if not ceiling:
        areas.append(SearchArea(Path.home(), 4))
    seen: set[Path] = set()
    return [a for a in areas if not (a.path in seen or seen.add(a.path))]


def choose_candidate(db: Session, row: ConnectDraft, choice: int) -> ConnectDraft:
    """The person said which of the folders they meant. Carry on with that one."""
    candidates = row.candidates or []
    if row.state != "choice_required" or not candidates:
        raise DraftError("There is nothing to choose here.", 409)
    if not 0 <= choice < len(candidates):
        raise DraftError("That isn't one of the folders Bevro found.", 422)
    row.target_kind = "local"
    row.target = str(candidates[choice]["path"])
    row.candidates = None
    row.state = "pending"  # the worker asks permission for it, or looks
    row.error = None
    db.commit()
    db.refresh(row)
    return row


def choices_public(row: ConnectDraft) -> list[dict[str, str]] | None:
    """What the person chooses between: a name and where it is, nothing else."""
    if row.state != "choice_required" or not row.candidates:
        return None
    return [{"label": str(c.get("label") or ""), "where": str(c.get("where") or "")} for c in row.candidates]


def grant_for_draft(db: Session, row: ConnectDraft, *, scope: str = "exact") -> ConnectDraft:
    """The person said yes. Write it down and carry on looking."""
    if row.state != "trust_required" or not row.trust:
        raise DraftError("There is nothing waiting to be allowed here.", 409)
    facts = dict(row.trust)
    kind = str(facts.get("kind") or trust_service.FOLDER)
    target = str(facts.get("path") or facts.get("program") or "")
    if scope == "parent" and kind == trust_service.FOLDER:
        target, scope = str(facts.get("parent") or target), "tree"
    try:
        # The worker resolved this path on the machine it is on; the API
        # records that decision rather than making it again.
        trust_service.grant(
            db,
            kind,
            target,
            scope="tree" if scope in ("tree", "parent") else "exact",
            label=facts.get("parent_label") if scope == "parent" else facts.get("label"),
            already_resolved=True,
        )
    except trust_service.TrustError as exc:
        raise DraftError(str(exc), 422) from None
    row.state = "pending"  # the worker looks again, now that it may
    row.trust = None
    row.error = None
    db.commit()
    db.refresh(row)
    return row


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
        text("SELECT id FROM connect_drafts WHERE state IN ('pending', 'testing') ORDER BY created_at LIMIT 5 FOR UPDATE SKIP LOCKED")
    ).all()  # local folders, commands being tested, and addresses the API could not reach
    handled = 0
    for (draft_id,) in rows:
        row = db.get(ConnectDraft, draft_id)
        if row is None:
            continue
        if row.state == "pending":
            _discover_into(row, trust_service.apply_to_process(db), {}, location=WORKER, db=db)
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
    if (needs_host(pd) and not can_discover_locally()) or api_ruled_out(pd):
        if not provider_service.worker_seen_recently(db):
            raise DraftError(UNREACHABLE_HERE_MESSAGE if api_ruled_out(pd) else WORKER_NEEDED_MESSAGE, 409)
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
    """The test, run from the host rather than the API.

    Commands and stdio MCP servers, because that is where the program is; and
    addresses the container cannot reach, because that is where they answer.
    A local MCP server is started once to read its tools (the person asked)."""
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
        # What was proved differs: that a program is there, or that something answered.
        worked = "Connection works." if kind in ("http", "openapi") else "The folder and the program are in place."
        row.test = {"ok": result.ok, "detail": result.detail or (worked if result.ok else "Not ready.")}
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
    pd = ProviderDraft.model_validate(row.draft).settled()
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
            # What a person reads, and separately what the thing said about
            # itself. A description the person typed is theirs and is kept.
            "description": (description if description is not None else pd.description).strip()[:2000],
            "source_description": pd.source_description,
            "source_name": pd.source_name,
            "enabled": enabled,
            "capabilities": capabilities,
            "adapter": adapter,
            "runtimes": runtimes,
            "active_runtime": active.id if active else None,
            # `kind` is how discovery read it ("http", "mcp"...); `target_kind`
            # is what the person typed ("url", "local", "command"), kept under
            # its own name so neither overwrites the other.
            "source": {"kind": row.target_kind, "target": row.target, **(pd.source or {}), "target_kind": row.target_kind, "validated_at": utcnow().isoformat()},
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
    provider.discovery_version = reconcile_service.DISCOVERY_VERSION
    db.commit()
    reconcile_service.reconcile(db, provider)
    db.refresh(provider)
    return provider


# --------------------------------------------------------------------------- reconnect

def reconnect_provider(db: Session, provider: Provider) -> Provider:
    """Discover the stored target again and replace the runtime profiles.

    Run wherever the thing can be reached. An address the container cannot
    see is handed to the worker on the host, and this call waits for it -
    the person pressed a button and is owed an answer, not a background job
    they have to go looking for.
    """
    # A project on this machine is the worker's to look at again, for the same
    # reason it was the worker's to find: the API in a container cannot see it.
    source = provider.source or {}
    kind = str(source.get("target_kind") or source.get("kind") or "")
    if kind in LOCAL_KINDS and not can_discover_locally():
        if not provider_service.worker_seen_recently(db):
            raise DraftError(WORKER_NEEDED_MESSAGE, 409)
        log.info("reconnect: %s is on this machine; asking the worker", provider.slug)
        return _reconnect_through_worker(db, provider, "it is on this machine")
    try:
        return _reconnect_here(db, provider)
    except NotReachable as exc:
        if not provider_service.worker_seen_recently(db):
            raise DraftError(UNREACHABLE_HERE_MESSAGE, 409) from None
        log.info("reconnect: %s is not reachable from here; asking the worker", provider.slug)
        return _reconnect_through_worker(db, provider, str(exc))


def rediscover(db: Session, provider: Provider) -> Provider:
    """Look at a provider again where it lives, and reconcile what is found.

    The same work Reconnect does, called by the worker when a provider's
    evidence predates what discovery now knows. Nothing the person set - its
    name, its credentials, its history - is touched.
    """
    return _reconnect_here(db, provider, location=WORKER)


def _reconnect_here(db: Session, provider: Provider, *, location: str = API, ruled_out: str | None = None) -> Provider:
    """The work itself, in whichever process can reach the target."""
    from app.services.runtime import set_runtimes

    source = provider.source or {}
    target_text = str(source.get("target") or "")
    if not target_text:
        raise DraftError("Bevro doesn't know where this was connected from. Connect it again instead.", 409)
    try:
        target = stored_target(str(source.get("target_kind") or source.get("kind") or ""), target_text)
    except TargetError as exc:
        raise DraftError(str(exc)) from exc
    try:
        # What the person has allowed, not what an environment variable says:
        # looking at a project again is looking at it, and the same permission
        # applies.
        draft = get_discovery_service().discover(target, DiscoveryContext(roots=trust_service.apply_to_process(db)))
    except NotReachable:
        raise
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
    # Who just reached it is worth remembering, exactly as at discovery:
    # otherwise the next run starts by trying somewhere already known to fail.
    for rt in draft.runtimes:
        rt.reachability = rt.reachability.with_result(location, True)
        if ruled_out:
            rt.reachability = rt.reachability.with_result(ruled_out, False)
    set_runtimes(provider, runtimes, active_id)
    # The service's own account of what it can do may have moved on: new
    # operations, renamed ones. The name stays as the person left it.
    if draft.capabilities:
        provider.capabilities = [c.model_dump() for c in draft.capabilities]
    provider.availability = None  # whoever can reach it reports afresh
    provider.discovery_version = reconcile_service.DISCOVERY_VERSION
    provider.source = {k: v for k, v in (provider.source or {}).items() if not k.startswith("reconnect_")}
    db.commit()
    # Nothing from the previous configuration survives a reconnect: which way
    # in is used is decided again, from what was just found.
    reconcile_service.reconcile(db, provider)
    db.refresh(provider)
    return provider


# How long the API waits for the host worker to do a reconnect it cannot do
# itself. Discovery through the worker takes seconds; this is the giving-up
# point, not the expected wait.
RECONNECT_WAIT_SECONDS = 30


def _reconnect_through_worker(db: Session, provider: Provider, reason: str) -> Provider:
    """Ask the worker to reconnect this, and wait for it to finish."""
    import time

    provider.source = {**(provider.source or {}), "reconnect_requested_at": utcnow().isoformat(), "reconnect_reason": reason}
    db.commit()
    deadline = time.monotonic() + RECONNECT_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(1.0)
        db.commit()  # end this transaction so the worker's write is visible
        db.refresh(provider)
        source = provider.source or {}
        if error := source.get("reconnect_error"):
            provider.source = {k: v for k, v in source.items() if not k.startswith("reconnect_")}
            db.commit()
            raise DraftError(str(error), 409)
        if not source.get("reconnect_requested_at"):
            db.refresh(provider)
            return provider
    provider.source = {k: v for k, v in (provider.source or {}).items() if not k.startswith("reconnect_")}
    db.commit()
    raise DraftError("The worker didn't respond. Is ./scripts/worker.sh running on this machine?", 409)


def run_reconnects(db: Session) -> int:
    """Worker entry point: reconnect providers the API could not reach itself."""
    from sqlalchemy import text

    rows = db.execute(
        text("SELECT id FROM providers WHERE source->>'reconnect_requested_at' IS NOT NULL ORDER BY updated_at LIMIT 5 FOR UPDATE SKIP LOCKED")
    ).all()
    done = 0
    for (provider_id,) in rows:
        provider = db.get(Provider, provider_id)
        if provider is None:
            continue
        try:
            # What the person has allowed, read again first: reconnecting is
            # looking at something, and looking needs permission.
            trust_service.apply_to_process(db)
            _reconnect_here(db, provider, location=WORKER, ruled_out=API)
        except (NotReachable, DiscoveryFailed, DraftError, TargetError) as exc:
            provider.source = {**(provider.source or {}), "reconnect_error": str(exc)}
            provider.source.pop("reconnect_requested_at", None)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - one bad provider must not stop the worker
            log.exception("reconnect on the host failed for %s", provider.slug)
            provider.source = {**(provider.source or {}), "reconnect_error": f"The reconnect failed: {type(exc).__name__}"}
            provider.source.pop("reconnect_requested_at", None)
            db.commit()
        done += 1
    return done


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
