"""Things Bevro knows how to use, offered - never added until the person says so.

Apps & agents is the person's own list. Support for something (a coding tool
Bevro can drive, say) is not an entry in it: it is offered here, added when
chosen, and removed like anything else.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.providers import ProviderOut
from app.schemas.serialise import provider_out
from app.services import providers as provider_service

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("")
def offered(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """What could be added, in words: name, what it does, what it can do."""
    return [
        {
            "slug": m["slug"],
            "name": m["name"],
            "description": m.get("description") or "",
            "capabilities": [c.get("id") for c in m.get("capabilities") or [] if isinstance(c, dict)],
        }
        for m in provider_service.offered_integrations(db)
    ]


@router.post("/{slug}", response_model=ProviderOut, status_code=201)
def add(slug: str, db: Session = Depends(get_db)) -> ProviderOut:
    """Add one because the person chose it."""
    provider = provider_service.add_integration(db, slug)
    if provider is None:
        raise HTTPException(404, "That isn't something Bevro can add, or it's already in your apps and agents.")
    db.commit()
    db.refresh(provider)
    return provider_out(provider, [], db)
