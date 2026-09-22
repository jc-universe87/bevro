"""Provider registry: what Bevro knows about who can do work."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters import ProviderSpec, get_adapter
from adapters.base import HealthResult, NotSupported
from adapters.registry import execution_mode
from adapters.runtime import RuntimeProfile
from app.models import Provider
from app.models._common import utcnow
from providers import load_manifests

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
        enabled=bool(data.get("enabled", True)),
        capabilities=list(data.get("capabilities") or []),
        adapter=dict(data.get("adapter") or {}),
        app_url=data.get("app_url") or None,
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


def seed_examples(db: Session) -> int:
    """Insert shipped providers that are missing and refresh the ones that exist.

    Manifests are the source of truth for shipped providers; `enabled` is the
    user's and is left alone. Returns how many were inserted.
    """
    added = 0
    for manifest in load_manifests():
        existing = get_by_slug(db, manifest["slug"])
        if existing is None:
            register_provider(db, manifest)
            added += 1
        elif existing.origin == "example":
            for field in _MANIFEST_FIELDS:
                value = manifest.get(field) if field != "description" else (manifest.get("description") or "")
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    if field == "adapter":
                        existing.runtimes = []  # re-derived below from the manifest's adapter
    db.commit()
    from app.services.runtime import ensure_runtimes

    ensure_runtimes(db)
    return added


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


def availability_of(provider: Provider) -> dict[str, Any]:
    """{"state": ..., "note": ...} as the interface should show it.

    Providers the API runs itself are available when enabled. Providers a
    worker runs are only available if a worker recently said so.
    """
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
    return {"state": state, "note": AVAILABILITY_NOTES[state]}


def is_available(provider: Provider) -> bool:
    return availability_of(provider)["state"] == "available"


def runs_in_background(provider: Provider) -> bool:
    from app.services.runtime import execution_of

    return execution_of(provider) == "background"


def worker_seen_recently(db: Session) -> bool:
    """Has a worker reported on any background provider lately? Used by Connect
    to know whether a local path can be handed to the host at all."""
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


SECRET_LABELS = {
    "OPENAI_API_KEY": "OpenAI credential",
    "ANTHROPIC_API_KEY": "Anthropic credential",
    "GOOGLE_API_KEY": "Google credential",
    "MISTRAL_API_KEY": "Mistral credential",
    "COHERE_API_KEY": "Cohere credential",
    "GROQ_API_KEY": "Groq credential",
    "api_key": "API token",
}


def secret_label(name: str) -> str:
    return SECRET_LABELS.get(name) or name.replace("_", " ").title()


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


def credential_status(provider: Provider, stored: list[str]) -> dict[str, str]:
    """name -> "bevro" | "host" | "project" | "missing".

    A stored secret is known here. The other sources live on the machine that
    runs the provider, so they come from the worker's last report; a project
    that carries its own .env is known from the adapter profile.
    """
    from adapters.runtime import NATIVE_STRATEGIES
    from app.services.runtime import active_runtime

    reported = (provider.availability or {}).get("credentials") or {}
    config = provider.adapter.get("config") or {}
    self_configured = {str(n) for n in config.get("self_configured") or []}
    rt = active_runtime(provider)
    native = rt is not None and rt.credentials.strategy in NATIVE_STRATEGIES
    out: dict[str, str] = {}
    for name in required_secrets(provider):
        if name in stored:
            out[name] = "bevro"
        elif reported.get(name) in ("host", "project"):
            out[name] = str(reported[name])
        elif name in self_configured or native:
            out[name] = "project"
        else:
            out[name] = "missing"
    return out


def credentials_of(provider: Provider, stored: list[str]) -> list[dict[str, Any]]:
    """[{name, label, present, source, status}] for the interface: what it needs and where it comes from."""
    return [
        {"name": n, "label": secret_label(n), "present": source != "missing", "source": source, "status": CREDENTIAL_SOURCE_WORDS[source]}
        for n, source in credential_status(provider, stored).items()
    ]


def providers_with_capability(db: Session, capability_id: str) -> list[Provider]:
    return [p for p in list_providers(db, enabled_only=True) if any(c.get("id") == capability_id for c in p.capabilities)]


def check_health(provider: Provider, secrets: dict[str, str]) -> HealthResult:
    from app.services.runtime import health

    return health(provider, secrets)
