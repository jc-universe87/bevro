from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import workspaces as workspace_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceOut(BaseModel):
    """Names only. Paths stay on the server."""

    id: uuid.UUID
    name: str
    description: str
    permissions: list[str]


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db)) -> list[WorkspaceOut]:
    return [
        WorkspaceOut(id=w.id, name=w.name, description=w.description, permissions=workspace_service.permission_sentences(list(w.permissions), w.name))
        for w in workspace_service.list_workspaces(db)
    ]
