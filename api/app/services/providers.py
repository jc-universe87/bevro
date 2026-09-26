"""Provider registry: what Bevro knows about who can do work."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters import ProviderSpec, get_adapter
from adapters.base import HealthResult, NotSupported
from adapters.registry import execution_mode
from adapters.runtime import RuntimeProfile
from app.config import get_settings
from app.models import Provider
from app.models._common import utcnow
from providers import DEMO, INTEGRATION, RETIRED_DEMOS, demo_slugs, load_manifests

log = logging.getLogger("bevro.providers")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "provider"


def unique_slug(db: Session, base: str) -> str:
    slug, n = base, 2
    while db.scalar(select(Provider.id).where(Provider.slug == slug)) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def list_providers(db: Session, *, enabled_only: bool = False) -> list[Provider]:
    stmt = select(Provider).order_by(Provider.created_at)
    if enabled_only:
        stmt = stmt.where(Provider.enabled.is_(True))
    return list(db.scalars(stmt))


def get_provider(db: Session, provider_id: uuid.UUID) -> Provider | None:
    return db.get(Provider, provider_id)


def get_by_slug(db: Session, slug: str) -> Provider | None:
    return db.scalar(select(Provider).where(Provider.slug == slug))


def register_provider(db: Session, data: dict[str, Any]) -> Provider:
    """Create a provider from a manifest-shaped dict. Caller commits."""
    slug = data.get("slug") or slugify(data["name"])
    provider = Provider(
        slug=unique_slug(db, slug),
        name=data["name"],
        description=data.get("description") or "",
        source_description=data.get("source_description") or None,
        source_name=data.get("source_name") or None,
        enabled=bool(data.get("enabled", True)),
        capabilities=list(data.get("capabilities") or []),
        adapter=dict(data.get("adapter") or {}),
        app_url=data.get("app_url") or None,
        surfaces=[x if isinstance(x, dict) else x.model_dump(mode="json", exclude_none=True) for x in data.get("surfaces") or []],
        icon=data.get("icon"),
        origin=data.get("origin") or "connected",
        source=data.get("source"),
    )
    from app.services.runtime import runtime_from_adapter, set_runtimes

    runtimes = [rt if isinstance(rt, RuntimeProfile) else RuntimeProfile.model_validate(rt) for rt in data.get("runtimes") or []]
    if not runtimes:
        runtimes = [runtime_from_adapter(dict(provider.adapter), origin=provider.origin)]
    set_runtimes(provider, runtimes, data.get("active_runtime"))
    db.add(provider)
    db.flush()
    return provider


_MANIFEST_FIELDS = ("name", "description", "capabilities", "adapter", "app_url", "icon")


class ProviderInUse(Exception):
    """Work is running through this provider right now."""


def removal_plan(db: Session, provider: Provider) -> dict[str, Any]:
    """What removing this agent would take with it, in plain terms.

    The work it did is never in this list: history belongs to the person, not
    to the agent that happened to do it.
    """
    from app.models import ProviderRun
    from app.domain.run_state import RUN_TERMINAL

    runs = db.scalars(select(ProviderRun).where(ProviderRun.provider_id == provider.id)).all()
    return {
        "history": len({r.task_id for r in runs}),
        "in_flight": sum(1 for r in runs if r.state not in RUN_TERMINAL),
        "credentials": len(provider.secrets),
        # A created agent keeps a project Bevro wrote and owns.
        "built_project": bool(agents_dir_for(provider).exists()),
        "built_connection": bool(integration_dir_for(provider).exists()),
    }


def agents_dir_for(provider: Provider):
    from app.services.agents import agent_dir

    return agent_dir(provider.id)


def integration_dir_for(provider: Provider):
    from app.services.bridges import integration_dir

    return integration_dir(provider.id)


def remove_provider(db: Session, provider: Provider) -> dict[str, Any]:
    """Remove an agent and everything that was only ever its. Caller commits.

    Gone: the provider, its credentials, its runtimes, its build records, and
    any project or connection Bevro generated for it - all of which live in
    folders named after it, so none of it is shared with anything else.

    Kept: every task, every run, every artifact. The runs simply lose the link
    and go on saying who did the work.
    """
    import shutil

    plan = removal_plan(db, provider)
    if plan["in_flight"]:
        raise ProviderInUse(f"{provider.name} is working on something right now. Wait for it to finish, or cancel it first.")

    for folder in (agents_dir_for(provider), integration_dir_for(provider)):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            log.info("provider %s: removed generated data at %s", provider.slug, folder.name)

    # Secrets and build records are the provider's own and cascade with it;
    # runs keep their row and lose only the link.
    db.delete(provider)
    db.flush()
    log.info("provider %s removed; %s task(s) of history kept", provider.slug, plan["history"])
    return plan


def shipped_demo(provider: Provider) -> bool:
    """Is this row a demo provider Bevro seeded, and no one has made their own?

    Three things must all agree: Bevro put it there (`origin == "example"`),
    the slug is one Bevro ships as a demo, and the way it is reached is still
    the shipped one. Someone's own agent called "Research" is connected or
    created, so it fails the first test and is never touched.
    """
    if provider.origin != "example":
        return False
    if provider.slug in RETIRED_DEMOS:
        adapter = provider.adapter or {}
        return adapter.get("kind") == "local" and adapter.get("ref") == RETIRED_DEMOS[provider.slug]
    if provider.slug not in demo_slugs():
        return False
    shipped = next((m for m in load_manifests(seed=DEMO) if m["slug"] == provider.slug), None)
    if shipped is None:
        return False
    adapter = provider.adapter or {}
    return adapter.get("kind") == shipped["adapter"].get("kind") and adapter.get("ref") == shipped["adapter"].get("ref")


def retire_demo_providers(db: Session, *, only_retired: bool = False) -> int:
    """Take the shipped demos out of a workspace that is not in demo mode -
    or, with `only_retired`, just the ones this release no longer ships.

    Only rows that are positively the untouched seeded demos. One that has
    done work cannot be deleted - its runs point at it - so it is disabled
    instead and stays readable in the history it belongs to.
    """
    from app.models import ProviderRun

    removed = 0
    for provider in list_providers(db, enabled_only=False):
        if not shipped_demo(provider) or (only_retired and provider.slug not in RETIRED_DEMOS):
            continue
        used = db.scalar(select(ProviderRun.id).where(ProviderRun.provider_id == provider.id).limit(1))
        if used is not None:
            if provider.enabled:
                provider.enabled = False
                log.info("demo provider %s has history; disabled rather than removed", provider.slug)
            continue
        db.delete(provider)
        removed += 1
        log.info("demo provider %s removed: this workspace is not in demo mode", provider.slug)
    db.flush()
    return removed


def seed_examples(db: Session, *, demo: bool | None = None) -> int:
    """Refresh the shipped providers someone already has, and - in demo mode
    only - register the examples.

    A normal workspace gets nothing it did not ask for: Apps & agents is the
    person's own list. Something Bevro knows how to use, such as a coding
    tool, is offered and added only when they choose it (`add_integration`);
    once removed, it stays removed. The demo providers are for tests,
    screenshots and `BEVRO_DEMO_MODE=true`.

    Manifests are the source of truth for shipped providers already added;
    `enabled` is the user's and is left alone. Returns how many were inserted.
    """
    if demo is None:
        demo = get_settings().demo_mode
    added = 0
    for manifest in load_manifests() if demo else load_manifests(seed=INTEGRATION):
        existing = get_by_slug(db, manifest["slug"])
        if existing is None:
            if demo:
                register_provider(db, {k: v for k, v in manifest.items() if k != "seed"})
                added += 1
        elif existing.origin == "example":
            for field in _MANIFEST_FIELDS:
                value = manifest.get(field) if field != "description" else (manifest.get("description") or "")
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    if field == "adapter":
                        existing.runtimes = []  # re-derived below from the manifest's adapter
    if not demo:
        added -= retire_demo_providers(db)
    else:
        retire_demo_providers(db, only_retired=True)
    db.commit()
    from app.services.runtime import ensure_runtimes

    ensure_runtimes(db)
    return max(added, 0)


def offered_integrations(db: Session) -> list[dict[str, Any]]:
    """What Bevro knows how to use and the person hasn't added: offered, never inserted."""
    return [m for m in load_manifests(seed=INTEGRATION) if get_by_slug(db, m["slug"]) is None]


def add_integration(db: Session, slug: str) -> Provider | None:
    """Add one offered integration because the person chose it. Caller commits."""
    manifest = next((m for m in offered_integrations(db) if m["slug"] == slug), None)
    if manifest is None:
        return None
    provider = register_provider(db, {k: v for k, v in manifest.items() if k != "seed"})
    from app.services.runtime import ensure_runtimes

    db.flush()
    ensure_runtimes(db)
    return provider


def to_spec(provider: Provider) -> ProviderSpec:
    from app.services.runtime import spec_for

    return spec_for(provider)


def supported_actions(provider: Provider) -> list[str]:
    """Which user-facing actions this provider really supports."""
    actions: list[str] = []
    if provider.enabled and provider.adapter.get("kind"):
        actions.append("ask")
    if provider.app_url:
        actions.append("open")
    return actions


# How long a worker's report counts as current. After that, the provider is
# "unavailable": nothing is running that could execute it.
AVAILABILITY_TTL_SECONDS = 150

AVAILABILITY_NOTES = {
    "available": None,
    "not_installed": "Not available on this installation",
    "not_authenticated": "Not signed in on this installation",
    "unavailable": "Not available on this installation",
}


def availability_of(provider: Provider, db: Session | None = None) -> dict[str, Any]:
    """{"state": ..., "note": ...} as the interface should show it.

    With a session, this is derived by `reconcile.state_of` - the one place
    that decides what a provider currently is. Without one (older callers,
    and the worker's own bookkeeping) the older reasoning still applies:
    providers the API runs itself are available when enabled, and providers a
    worker runs are available only if a worker recently said so.
    """
    if db is not None:
        # Worked out from the evidence, in one place, so that nothing can say
        # "waiting for the worker" and "reached from this machine" at once.
        from app.services.reconcile import CONNECTION_WORDS, state_of

        state = state_of(db, provider)
        # `reason` lets the page choose the one thing to do about it; the
        # words stay in `note`.
        return {"state": "available" if state.available else "unavailable", "note": None if state.available else CONNECTION_WORDS.get(state.connection), "reason": state.connection}
    if not provider.enabled:
        return {"state": "unavailable", "note": "Paused"}
    from app.services.runtime import active_runtime, execution_of

    kind = str(provider.adapter.get("kind", ""))
    try:
        get_adapter(kind)
    except NotSupported:
        return {"state": "unavailable", "note": None if kind == "declared" else AVAILABILITY_NOTES["unavailable"]}
    from app.services.runtime import reachable_without_worker

    rt = active_runtime(provider)
    if rt is not None and rt.availability == "needs_start":
        return {"state": "unavailable", "note": "Not running"}
    # Anything this process can drive itself is available now; only a provider
    # that can *only* be reached from the host waits on a worker's report.
    if execution_of(provider) != "background" or reachable_without_worker(provider):
        return {"state": "available", "note": None}
    report = provider.availability or {}
    if not report and provider.origin == "connected":
        # Just connected; the host worker reports within a minute.
        return {"state": "unavailable", "note": "Waiting for the worker on this machine"}
    checked_at = report.get("checked_at")
    fresh = False
    if isinstance(checked_at, str):
        try:
            fresh = (utcnow() - datetime.fromisoformat(checked_at)).total_seconds() < AVAILABILITY_TTL_SECONDS
        except ValueError:
            fresh = False
    state = str(report.get("state") or "unavailable") if fresh else "unavailable"
    if state not in AVAILABILITY_NOTES:
        state = "unavailable"
    note = AVAILABILITY_NOTES[state]
    if state == "unavailable" and not report:
        # Nothing has ever reported on it. On a fresh installation that is not
        # a fault - the optional host worker simply is not running - and
        # saying so is kinder than "not available".
        note = "Optional \u00b7 needs the host worker"
    return {"state": state, "note": note}


def is_available(provider: Provider) -> bool:
    return availability_of(provider)["state"] == "available"


def runs_in_background(provider: Provider) -> bool:
    from app.services.runtime import execution_of

    return execution_of(provider) == "background"


WORKER_TTL_SECONDS = 180


def record_heartbeat(db: Session, worker_id: str, kinds: list[str], *, network: str = "host") -> None:
    """A worker saying it is running, and where it sits."""
    from app.models import WorkerHeartbeat

    row = db.get(WorkerHeartbeat, worker_id)
    if row is None:
        row = WorkerHeartbeat(worker_id=worker_id)
        db.add(row)
    row.seen_at = utcnow()
    row.kinds = list(kinds)
    row.network = network
    # A worker killed outright leaves its row behind. One sweep per heartbeat
    # keeps the table the size of the workers actually running.
    from sqlalchemy import delete as sa_delete

    db.execute(sa_delete(WorkerHeartbeat).where(WorkerHeartbeat.seen_at < utcnow() - timedelta(seconds=WORKER_TTL_SECONDS * 4)))
    db.commit()


def worker_on_the_host(db: Session) -> bool:
    """Is a worker running on the machine itself right now?

    This is the question behind "can an address the container cannot reach be
    tried from somewhere else", so it is asked of the workers themselves.
    """
    from sqlalchemy import select as sa_select

    from app.models import WorkerHeartbeat

    cutoff = utcnow() - timedelta(seconds=WORKER_TTL_SECONDS)
    return db.execute(sa_select(WorkerHeartbeat.worker_id).where(WorkerHeartbeat.network == "host", WorkerHeartbeat.seen_at >= cutoff).limit(1)).first() is not None


def settle_descriptions(db: Session) -> int:
    """Bring anything connected before all this up to the same standard.

    Three things, each of which leaves what someone wrote alone:

    * the service's own integration prose moves out of `description` and into
      `source_description`, where Advanced details shows it;
    * a name that ends by describing its own interface is trimmed, and the
      exact one it gave is kept;
    * capabilities described only by their tag are read again from the
      operation catalogue Bevro already has, so they say what they do.

    Copy Bevro generated is not kept at all: `description` ends up empty and
    the sentence is worked out fresh each time, which is why improving the
    wording improves what is already connected.
    """
    from app.connect import copy

    settled = 0
    for provider in list_providers(db, enabled_only=False):
        changed = False

        if provider.origin == "connected":
            trimmed = copy.display_name(provider.name)
            if trimmed != provider.name:
                # Keep the exact name it gave, then show the shorter one.
                provider.source_name = provider.source_name or provider.name
                provider.name = trimmed[:120]
                changed = True

        if not copy.is_fit_to_show(provider.description):
            if provider.source_description is None and provider.description:
                provider.source_description = provider.description[:4000]
            if provider.description:
                provider.description = ""
                changed = True
        elif provider.source_description is not None and provider.origin == "connected":
            # A sentence Bevro wrote itself, from a time when it wrote them
            # down. Derived again on the way out from now on.
            provider.description = ""
            changed = True

        if _reread_capabilities(provider):
            changed = True
        settled += 1 if changed else 0
    if settled:
        db.commit()
        log.info("brought %s provider(s) up to the current wording", settled)
    return settled


def _reread_capabilities(provider: Provider) -> bool:
    """Say what each group of operations does, where only its tag was stored."""
    from app.connect.openapi import CAPABILITY_WORDING, capabilities_from_operations, catalogue_from_json

    config = provider.adapter.get("config") or {}
    current = all(c.get("wording") == CAPABILITY_WORDING for c in provider.capabilities if isinstance(c, dict))
    if not config.get("operations") or (current and provider.capabilities):
        return False

    reread = capabilities_from_operations(catalogue_from_json(config.get("operations")))
    if not reread:
        return False
    provider.capabilities = reread
    return True


def worker_seen_recently(db: Session) -> bool:
    """Is there a worker to hand something to?

    A worker says so itself. Availability reports are still read as a second
    answer, so an older worker that does not send heartbeats keeps working.
    """
    if worker_on_the_host(db):
        return True
    for provider in list_providers(db):
        if not runs_in_background(provider):
            continue
        checked_at = (provider.availability or {}).get("checked_at")
        if not isinstance(checked_at, str):
            continue
        try:
            if (utcnow() - datetime.fromisoformat(checked_at)).total_seconds() < AVAILABILITY_TTL_SECONDS:
                return True
        except ValueError:
            continue
    return False


def record_availability(db: Session, provider: Provider, result: HealthResult) -> None:
    """Called by the process that can actually reach the provider (the worker)."""
    state = result.state or ("available" if result.ok else "unavailable")
    report: dict[str, Any] = {"state": state, "checked_at": utcnow().isoformat(), "detail": result.detail}
    if result.credentials:
        report["credentials"] = dict(result.credentials)  # sources by name; never values
    provider.availability = report
    db.commit()


from app.connect.draft import SECRET_LABELS, secret_label  # noqa: E402,F401 - the one table of credential names


def required_secrets(provider: Provider) -> list[str]:
    """Secret names the adapter profile will use, in order. Values never appear here."""
    config = provider.adapter.get("config") or {}
    names: list[str] = [str(n) for n in config.get("secret_env") or []]
    auth = config.get("auth") or {}
    if isinstance(auth, dict) and auth.get("secret"):
        names.append(str(auth["secret"]))
    return list(dict.fromkeys(names))


CREDENTIAL_SOURCE_WORDS = {
    "bevro": "Added",
    "host": "From this machine",
    "project": "Uses its own",
    "missing": "Missing",
}


def report_is_fresh(report: dict[str, Any] | None) -> bool:
    """Did a worker say this recently enough to still mean anything?"""
    checked_at = (report or {}).get("checked_at")
    if not isinstance(checked_at, str):
        return False
    try:
        return (utcnow() - datetime.fromisoformat(checked_at)).total_seconds() < AVAILABILITY_TTL_SECONDS
    except ValueError:
        return False


def credential_status(provider: Provider, stored: list[str]) -> dict[str, str]:
    """name -> "bevro" | "host" | "project" | "missing".

    A stored secret is known here. The other sources live on the machine that
    runs the provider, so they come from the worker's last report; a project
    that carries its own .env is known from the adapter profile.
    """
    from adapters.runtime import NATIVE_STRATEGIES
    from app.services.runtime import active_runtime, runtimes_of

    reported = (provider.availability or {}).get("credentials") or {}
    config = provider.adapter.get("config") or {}
    self_configured = {str(n) for n in config.get("self_configured") or []}
    rt = active_runtime(provider)
    native = rt is not None and rt.credentials.strategy in NATIVE_STRATEGIES
    # Any way into this provider having it is enough for the provider to have
    # it. Which ways in can and cannot is a fact about each of them, kept on
    # each of them, and shown under Advanced details - but nobody is asked
    # for a credential this machine already holds.
    by_a_runtime = {name for profile in runtimes_of(provider) if profile.invocable for name in profile.credentials.supplied}
    out: dict[str, str] = {}
    for name in required_secrets(provider):
        if name in stored:
            out[name] = "bevro"
        elif reported.get(name) in ("host", "project"):
            out[name] = str(reported[name])
        elif name in self_configured or name in by_a_runtime or native:
            out[name] = "project"
        else:
            out[name] = "missing"
    return out


def credentials_of(provider: Provider, stored: list[str]) -> list[dict[str, Any]]:
    """[{name, label, present, source, status, note}] - what it needs, where
    it comes from, and, when the answer is "nowhere", what Bevro did find.

    A project whose installed service holds the key is not the same as one
    where nothing has it, and saying only "Missing" would throw away the
    difference.
    """
    from app.services.runtime import active_runtime

    from app.connect.runtimes import credential_story

    rt = active_runtime(provider)
    # Only when there is something to add. "Missing" already says missing;
    # the note is for when Bevro found the credential somewhere it cannot use,
    # and "why" for anyone who asks why that one can't simply be used.
    explanation, why = credential_story(provider.name, rt.credentials) if rt is not None and rt.credentials.supplied_elsewhere else (None, None)
    return [
        {
            "name": n,
            "label": secret_label(n),
            "present": source != "missing",
            "source": source,
            "status": CREDENTIAL_SOURCE_WORDS[source],
            "note": explanation if source == "missing" else None,
            "why": why if source == "missing" else None,
        }
        for n, source in credential_status(provider, stored).items()
    ]


def providers_with_capability(db: Session, capability_id: str) -> list[Provider]:
    return [p for p in list_providers(db, enabled_only=True) if any(c.get("id") == capability_id for c in p.capabilities)]


def check_health(provider: Provider, secrets: dict[str, str]) -> HealthResult:
    from app.services.runtime import health

    return health(provider, secrets)
