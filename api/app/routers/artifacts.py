from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Artifact
from app.schemas.serialise import artifact_out
from app.schemas.tasks import ArtifactOut
from app.services.artifacts import resolve_path

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: uuid.UUID, db: Session = Depends(get_db)) -> ArtifactOut:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Result not found.")
    return artifact_out(artifact)


@router.get("/{artifact_id}/content")
def get_artifact_content(artifact_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Result not found.")
    path = resolve_path(artifact)
    if path is None:
        raise HTTPException(404, "This result has no stored file.")
    return FileResponse(path, media_type=artifact.mime_type or "application/octet-stream", filename=path.name.split("-", 5)[-1])
