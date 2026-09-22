"""The workspace registry: approved directories coding providers may use.

Rows are seeded from a server-side JSON file (BEVRO_WORKSPACES_FILE). The
browser only ever sees id, name and description. Nothing in this module
accepts a path from a request.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Workspace

log = logging.getLogger("bevro.workspaces")
ALLOWED_PERMISSIONS = ("read", "write", "run_commands")


def load_config(path: str | None = None) -> list[dict[str, Any]]:
    file = Path(path or get_settings().workspaces_file)
    if not file.is_file():
        return []
    with file.open(encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("workspaces", []) if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict) and e.get("slug") and e.get("path")]


def seed_from_config(db: Session, path: str | None = None) -> int:
    """Insert or update workspaces from the config file. The file is the source of truth."""
    changed = 0
    for entry in load_config(path):
        permissions = [p for p in entry.get("permissions", ["read"]) if p in ALLOWED_PERMISSIONS] or ["read"]
        read_paths = [str(p) for p in entry.get("read_paths", []) if isinstance(p, str)]
        row = db.scalar(select(Workspace).where(Workspace.slug == entry["slug"]))
        values = {
            "name": entry.get("name") or entry["slug"],
            "path": str(entry["path"]),
            "description": entry.get("description") or "",
            "enabled": bool(entry.get("enabled", True)),
            "permissions": permissions,
            "read_paths": read_paths,
            "repository": dict(entry.get("repository") or {}),
        }
        if row is None:
            db.add(Workspace(slug=entry["slug"], **values))
            changed += 1
        else:
            for key, value in values.items():
                if getattr(row, key) != value:
                    setattr(row, key, value)
                    changed += 1
    db.commit()
    return changed


def list_workspaces(db: Session, *, enabled_only: bool = True, include_internal: bool = False) -> list[Workspace]:
    """The projects a coding provider may work in. Workspaces Bevro made for
    itself (bridge folders) are left out unless asked for."""
    stmt = select(Workspace).order_by(Workspace.name)
    if enabled_only:
        stmt = stmt.where(Workspace.enabled.is_(True))
    if not include_internal:
        stmt = stmt.where(Workspace.internal.is_(False))
    return list(db.scalars(stmt))


def get_workspace(db: Session, workspace_id: uuid.UUID | str) -> Workspace | None:
    try:
        key = uuid.UUID(str(workspace_id))
    except ValueError:
        return None
    return db.get(Workspace, key)


def resolve_workspace(db: Session, value: Any) -> Workspace | None:
    """Only an id of an enabled workspace resolves. Paths, slugs, names never do."""
    if not isinstance(value, str) or "/" in value or "\\" in value:
        return None
    ws = get_workspace(db, value)
    return ws if ws is not None and ws.enabled else None


def infer_from_text(db: Session, text: str) -> Workspace | None:
    """If the request names exactly one workspace, use it."""
    matches = []
    lowered = text.lower()
    for ws in list_workspaces(db):
        pattern = r"\b" + re.escape(ws.name.lower()) + r"\b"
        if re.search(pattern, lowered):
            matches.append(ws)
    return matches[0] if len(matches) == 1 else None


def as_choice_options(workspaces: list[Workspace]) -> list[dict[str, str]]:
    return [{"value": str(ws.id), "label": ws.name} for ws in workspaces]


def create_internal(db: Session, *, slug: str, name: str, path: str, read_paths: list[str] | None = None, permissions: list[str] | None = None, description: str = "") -> Workspace:
    """A workspace Bevro makes for its own work (building a bridge, say).

    Not offered to the person as a project, and replaced rather than
    duplicated when the same work is done again. Caller commits.
    """
    row = db.scalar(select(Workspace).where(Workspace.slug == slug))
    values = {
        "name": name,
        "path": path,
        "description": description,
        "enabled": True,
        "permissions": [p for p in (permissions or ["read", "write", "run_commands"]) if p in ALLOWED_PERMISSIONS],
        "read_paths": list(read_paths or []),
        "internal": True,
        "repository": {},
    }
    if row is None:
        row = Workspace(slug=slug, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.flush()
    return row


def permission_sentences(permissions: list[str], workspace_name: str) -> list[str]:
    """Plain wording for the permissions summary."""
    out: list[str] = []
    if "write" in permissions:
        out.append(f"Read and modify files in {workspace_name}")
    elif "read" in permissions:
        out.append(f"Read files in {workspace_name}")
    if "run_commands" in permissions:
        out.append("Run project commands")
    return out


# A workspace path may be written as "@integrations/<provider-id>" or
# "@agents/<provider-id>/versions/<build>", meaning "Bevro's own folder for
# that purpose". Each process resolves it against its own settings, so the API
# (in a container) and the worker (on the host) can name the same folder
# without agreeing on absolute paths.
INTEGRATIONS_PREFIX = "@integrations/"
AGENTS_PREFIX = "@agents/"


def resolve_path(path: str) -> str:
    settings = get_settings()
    for prefix, root in ((INTEGRATIONS_PREFIX, settings.integrations_dir), (AGENTS_PREFIX, settings.agents_dir)):
        if path.startswith(prefix):
            return f"{root.rstrip('/')}/{path[len(prefix):]}"
    return path


def to_input(ws: Workspace) -> dict[str, Any]:
    """What an adapter receives about a workspace. Built server-side at run time."""
    return {
        "id": str(ws.id),
        "name": ws.name,
        "path": resolve_path(ws.path),
        "permissions": list(ws.permissions),
        "read_paths": [resolve_path(p) for p in (ws.read_paths or [])],
        "repository": dict(ws.repository or {}),
    }
