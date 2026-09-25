"""Access and trust: what the person has allowed Bevro to touch here.

Everything on this page was agreed to once, in the browser, while connecting
something. Revoking one takes effect the next time anything is looked at or
run - there is nothing to restart, and nothing to edit.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import trust as trust_service

router = APIRouter(prefix="/trust", tags=["trust"])


class GrantOut(BaseModel):
    id: str
    kind: str          # "folder" | "command" | "context"
    label: str
    target: str        # a folder's path; a command's program name, never its arguments
    scope: str
    granted_at: str | None = None
    granted_by: str
    revoked_at: str | None = None


class AccessOut(BaseModel):
    grants: list[GrantOut]
    # When an administrator has set a boundary, the folders a grant must sit
    # inside. Empty on an ordinary installation.
    ceiling: list[str]


@router.get("", response_model=AccessOut)
def listing(db: Session = Depends(get_db)) -> AccessOut:
    return AccessOut(
        grants=[GrantOut(**trust_service.public(row)) for row in trust_service.active_grants(db)],
        ceiling=[str(path) for path in trust_service.admin_ceiling()],
    )


@router.delete("/{grant_id}", status_code=204)
def revoke(grant_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    if trust_service.revoke(db, grant_id) is None:
        raise HTTPException(404, "That permission wasn't found.")
    # What a provider can do may have changed with it.
    from app.services.reconcile import reconcile_all

    reconcile_all(db)
