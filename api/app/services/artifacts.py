"""Turns an adapter's ArtifactDraft into a stored Artifact.

Storage in V1 is the filesystem under BEVRO_ARTIFACT_DIR. Inline content is
written there; payloads and links stay in the database.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from adapters import ArtifactDraft
from app.config import get_settings
from app.models import Artifact

# Artifact types Bevro can render itself. Anything else falls back to "Open result".
KNOWN_TYPES = frozenset({"text", "note", "file", "report", "image", "structured", "interactive", "deep_link", "mini_app", "diff"})

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str | None, fallback: str) -> str:
    cleaned = _SAFE_NAME.sub("-", name or "").strip("-.")
    return cleaned or fallback


def artifact_root() -> Path:
    root = Path(get_settings().artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_draft(db: Session, task_id: uuid.UUID, run_id: uuid.UUID | None, draft: ArtifactDraft) -> Artifact:
    artifact = Artifact(
        task_id=task_id,
        provider_run_id=run_id,
        type=draft.type,
        title=draft.title,
        summary=draft.summary,
        mime_type=draft.mime_type,
        payload=draft.payload,
        external_url=draft.external_url,
        meta=dict(draft.metadata),
    )
    db.add(artifact)
    db.flush()
    if draft.content is not None:
        rel = Path(str(task_id)) / f"{artifact.id}-{_safe_filename(draft.filename, 'result.bin')}"
        target = artifact_root() / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(draft.content)
        artifact.storage_path = str(rel)
    return artifact


def resolve_path(artifact: Artifact) -> Path | None:
    if not artifact.storage_path:
        return None
    path = (artifact_root() / artifact.storage_path).resolve()
    if artifact_root().resolve() not in path.parents:
        return None
    return path if path.is_file() else None


def is_known_type(artifact_type: str) -> bool:
    return artifact_type in KNOWN_TYPES
