"""The sanitised provider catalogue a router is allowed to see.

Built from the registry for every routing call. It carries what is needed to
choose a provider and nothing that could leak: no adapter configuration, no
URLs, no secrets, no workspace paths, no worker details.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from adapters import get_adapter
from adapters.base import NotSupported
from app.models import Provider
from app.services.providers import availability_of, list_providers

MAX_DESCRIPTION = 200
MAX_CAPABILITY_TEXT = 160


class CatalogueCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    description: str | None = None
    # The service's other words for this same ability, from its own addresses.
    # Kept out of the prompt: a model finds synonyms by itself, and the
    # deterministic router is the one that needs them written down.
    terms: list[str] = Field(default_factory=list)


class CatalogueEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # the provider slug; stable and readable
    name: str
    description: str
    capabilities: list[CatalogueCapability] = Field(default_factory=list)
    available: bool
    can_invoke: bool
    # What Bevro must have before this provider can start, e.g. ["workspace"].
    requires: list[str] = Field(default_factory=list)
    constraints: str | None = None


def _clip(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def entry_for(provider: Provider) -> CatalogueEntry:
    from app.services.runtime import active_runtime, execution_of, requires_of

    kind = str(provider.adapter.get("kind", ""))
    requires: list[str] = []
    can_invoke = False
    constraints = None
    try:
        get_adapter(kind)
        rt = active_runtime(provider)
        can_invoke = rt.abilities.accepts_prompt if rt else True
        requires = sorted(requires_of(provider))
        # How long it may take is worth knowing when choosing; *where* it runs
        # is not. A router picks what can do the work, and Bevro decides which
        # of its processes drives it - the catalogue never says.
        if execution_of(provider) == "background":
            constraints = "Long-running; works inside one approved project directory." if "workspace" in requires else "May take a while."
    except NotSupported:
        pass
    return CatalogueEntry(
        id=provider.slug,
        name=_clip(provider.name, 120),
        description=_clip(provider.description, MAX_DESCRIPTION),
        capabilities=[
            CatalogueCapability(
                id=_clip(c.get("id"), 80),
                title=_clip(c.get("title"), 80) or None,
                description=_clip(c.get("description"), MAX_CAPABILITY_TEXT) or None,
                terms=[_clip(t, 40) for t in (c.get("terms") or []) if isinstance(t, str)][:6],
            )
            for c in provider.capabilities
            if isinstance(c, dict) and c.get("id")
        ],
        available=availability_of(provider)["state"] == "available",
        can_invoke=can_invoke,
        requires=requires,
        constraints=constraints,
    )


def build_catalogue(db: Session, *, selectable_only: bool = True) -> list[CatalogueEntry]:
    """Providers a router may choose from. By default only ones that can run now."""
    entries = [entry_for(p) for p in list_providers(db, enabled_only=True)]
    if selectable_only:
        entries = [e for e in entries if e.available and e.can_invoke]
    return entries


def catalogue_json(entries: list[CatalogueEntry]) -> list[dict[str, Any]]:
    return [e.model_dump(exclude_none=True, exclude={"capabilities": {"__all__": {"terms"}}}) for e in entries]
