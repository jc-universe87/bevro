"""What Bevro is allowed to look at and run on this machine, and who said so.

The person types where something is. Bevro works out what it is, and if it is
on this machine it asks - once, in plain words, about that one thing - and
keeps the answer. Nothing here is configured in advance.

Three rules hold the whole thing up:

    narrow by default   a folder is granted, not its parent. A parent is a
                        separate decision, made deliberately.
    canonical           symlinks are resolved before a grant is written, so
                        what was approved cannot be made to mean something
                        else afterwards.
    checked at use      the worker reads these when it is about to look at or
                        run something, not when discovery happened. Revoking
                        takes effect on the next use, without a restart.

`BEVRO_LOCAL_ROOTS` still exists and now means the ceiling: on an
installation where it is set, even something the person approved has to sit
inside it. Where it is unset - the ordinary self-hosted case - the person's
own decisions are the whole policy.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.localroots import managed_roots, parse_roots
from app.config import get_settings
from app.models import TrustGrant
from app.models._common import utcnow

log = logging.getLogger("bevro.trust")

FOLDER = "folder"
COMMAND = "command"

# Places nothing should be granted, whoever asks. These are the machine's own
# workings and the person's keys: a folder inside one of them is not a project
# anyone meant to connect.
NEVER = (
    "/etc", "/root", "/proc", "/sys", "/dev", "/boot", "/bin", "/sbin", "/lib", "/lib64",
    "/usr/bin", "/usr/sbin", "/usr/lib", "/var/lib", "/var/run", "/run", "/snap",
    "/System", "/Library", "/Windows", "/Program Files",
)
# Directly inside the home directory, these hold credentials rather than work.
NEVER_IN_HOME = (".ssh", ".gnupg", ".aws", ".config/gcloud", ".kube", ".docker", ".password-store", ".bevro")
# Granting one of these would be granting the machine.
TOO_BROAD = ("/", "/home", "/Users", "/mnt", "/media", "/opt", "/srv", "/var", "/tmp", "/usr")


class TrustError(Exception):
    """A grant Bevro will not make. The message is for the person."""


# --------------------------------------------------------------------------- the ceiling

def admin_ceiling() -> list[Path]:
    """The administrator's boundary, if this installation has one.

    Two things at once, deliberately: the folders named here are allowed
    without anyone being asked, and nothing outside them can be granted by
    anyone. An installation that sets nothing has no ceiling, and what the
    person agrees to in the browser is the whole policy.
    """
    return parse_roots(get_settings().local_roots or os.environ.get("BEVRO_LOCAL_ROOTS"))


def inside_ceiling(path: Path) -> bool:
    ceiling = admin_ceiling()
    if not ceiling:
        return True  # no ceiling set: the person's own decisions are the policy
    return any(path == root or root in path.parents for root in ceiling)


# --------------------------------------------------------------------------- looking at a path

def canonical(raw: str) -> Path:
    """The real path this text means, with ~ expanded and symlinks resolved.

    Resolving first is the point: a grant is written against where something
    actually is, so a link pointed somewhere else later grants nothing.
    """
    if not raw or not isinstance(raw, str) or "\0" in raw:
        raise TrustError("That isn't a path.")
    path = Path(os.path.expanduser(raw.strip()))
    if not path.is_absolute():
        raise TrustError("Give the full path, starting from the top of the filesystem.")
    try:
        return path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise TrustError("There is nothing at that path on this machine.") from exc


def refuse_reason(path: Path) -> str | None:
    """Why Bevro will not be given this, in words for the person."""
    text = str(path)
    if text in TOO_BROAD:
        return "That is the whole machine, or close to it. Choose the folder the project is actually in."
    for forbidden in NEVER:
        if text == forbidden or text.startswith(forbidden + "/"):
            return "That folder belongs to the operating system. Choose a project folder instead."
    home = Path(os.path.expanduser("~"))
    for private in NEVER_IN_HOME:
        candidate = home / private
        if text == str(candidate) or text.startswith(str(candidate) + "/"):
            return "That folder holds credentials rather than a project."
    if not inside_ceiling(path):
        return "This installation only allows folders inside the ones its administrator set."
    return None


def look_at(raw: str) -> dict[str, Any]:
    """What can safely be said about a path, for the question Bevro asks.

    Runs where the filesystem is - the worker, on the host. The API inside a
    container has no business guessing at any of this.
    """
    try:
        path = canonical(raw)
    except TrustError as exc:
        return {"kind": FOLDER, "exists": False, "reason": str(exc)}
    return {
        "kind": FOLDER,
        "exists": True,
        "is_directory": path.is_dir(),
        "path": str(path),
        "label": path.name or str(path),
        "parent": str(path.parent),
        "parent_label": path.parent.name or str(path.parent),
        "reason": refuse_reason(path),
    }


def look_at_command(argv: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """The same, for a program: what it is, without its arguments."""
    words = [str(w) for w in argv if str(w)]
    if not words:
        return {"kind": COMMAND, "exists": False, "reason": "There is no command there."}
    program = words[0]
    found = Path(program).resolve() if "/" in program else _on_the_path(program)
    return {
        "kind": COMMAND,
        # Only the program, never the arguments: an argument may be a secret.
        "label": Path(program).name,
        "program": program,
        "exists": found is not None,
        "path": str(found) if found else None,
        "reason": None if found else f"Bevro can't find {Path(program).name} on this machine.",
    }


def _on_the_path(program: str) -> Path | None:
    import shutil

    found = shutil.which(program)
    return Path(found).resolve() if found else None


# --------------------------------------------------------------------------- grants

def active_grants(db: Session, kind: str | None = None) -> list[TrustGrant]:
    statement = select(TrustGrant).where(TrustGrant.revoked_at.is_(None)).order_by(TrustGrant.granted_at)
    if kind:
        statement = statement.where(TrustGrant.kind == kind)
    return list(db.execute(statement).scalars())


def grant(db: Session, kind: str, target: str, *, scope: str = "exact", label: str | None = None, granted_by: str = "person", already_resolved: bool = False) -> TrustGrant:
    """Write down that this is allowed. Caller commits.

    `already_resolved` is how the API records what the worker established.
    The host is where the filesystem is, so it is the host that followed the
    symlinks and decided what the path really means; the API has no business
    doing it again from inside a container where the path does not exist.
    The rules that are about the path rather than the disk - the machine's
    own folders, the administrator's boundary - are applied either way.
    """
    if kind == FOLDER:
        path = Path(target) if already_resolved else canonical(target)
        if not path.is_absolute():
            raise TrustError("Give the full path, starting from the top of the filesystem.")
        if refusal := refuse_reason(path):
            raise TrustError(refusal)
        target = str(path)
        label = label or path.name or target
    elif kind == COMMAND:
        target = str(target).strip()
        if not target:
            raise TrustError("There is no command there.")
        label = label or Path(target).name
    else:
        raise TrustError(f"Bevro has nothing to grant for {kind!r}.")

    existing = db.execute(select(TrustGrant).where(TrustGrant.kind == kind, TrustGrant.target == target)).scalar_one_or_none()
    if existing is not None:
        existing.revoked_at = None
        existing.scope = scope
        existing.label = label
        existing.granted_at = utcnow()
        existing.granted_by = granted_by
        return existing
    row = TrustGrant(kind=kind, target=target, scope=scope, label=label, granted_by=granted_by, granted_at=utcnow())
    db.add(row)
    db.flush()
    return row


def revoke(db: Session, grant_id: uuid.UUID) -> TrustGrant | None:
    row = db.get(TrustGrant, grant_id)
    if row is None or row.revoked_at is not None:
        return row
    row.revoked_at = utcnow()
    db.commit()
    return row


# --------------------------------------------------------------------------- what the grants allow

def folder_roots(db: Session) -> list[Path]:
    """Every folder Bevro may work in, from the grants and its own directories.

    An "exact" grant covers the folder and what is under it - a project has
    subdirectories - and nothing above it. A "tree" grant is the same thing
    said about a parent the person chose deliberately; the distinction is
    kept for Settings to show, not for the check.
    """
    roots: list[Path] = []
    for row in active_grants(db, FOLDER):
        try:
            path = Path(row.target)
        except (TypeError, ValueError):
            continue
        if inside_ceiling(path) and path not in roots:
            roots.append(path)
    # A folder an administrator named is a folder an administrator allowed.
    # Where BEVRO_LOCAL_ROOTS is set it is both the boundary and a standing
    # yes to the folders in it; anywhere else inside it still needs asking.
    for root in [*admin_ceiling(), *managed_roots()]:
        if root not in roots:
            roots.append(root)
    return roots


def allows_folder(db: Session, raw: str) -> bool:
    try:
        path = canonical(raw)
    except TrustError:
        return False
    return any(path == root or root in path.parents for root in folder_roots(db))


def allows_command(db: Session, argv: list[str] | tuple[str, ...]) -> bool:
    """Is this program one the person agreed Bevro could run?

    The program only. Its arguments are the caller's business and may hold a
    secret, so they are neither stored nor compared.

    An installation whose administrator set a boundary for local work has
    already answered this: drawing that boundary is what allowing local
    execution means. Everywhere else, the person is asked.
    """
    words = [str(w) for w in argv if str(w)]
    if not words:
        return False
    if admin_ceiling():
        return True
    program = words[0]
    names = {program, Path(program).name}
    resolved = _on_the_path(program) if "/" not in program else Path(program)
    if resolved is not None:
        names |= {str(resolved), resolved.name}
    return any(row.target in names or Path(row.target).name in names for row in active_grants(db, COMMAND))


def apply_to_process(db: Session) -> list[Path]:
    """Tell this process what it may touch, from what is written down.

    Called by the worker before it looks at or runs anything, which is what
    makes a revoke take effect without a restart.
    """
    from adapters import localroots

    roots = folder_roots(db)
    localroots.set_effective_roots(roots)
    return roots


def public(row: TrustGrant) -> dict[str, Any]:
    """What Settings may show: the folder, never a command's arguments."""
    return {
        "id": str(row.id),
        "kind": row.kind,
        "label": row.label or Path(row.target).name,
        "target": row.target if row.kind == FOLDER else Path(row.target).name,
        "scope": row.scope,
        "granted_at": row.granted_at.isoformat() if isinstance(row.granted_at, datetime) else None,
        "granted_by": row.granted_by,
        "revoked_at": row.revoked_at.isoformat() if isinstance(row.revoked_at, datetime) else None,
    }


# --------------------------------------------------------------------------- what was already here

def adopt_existing(db: Session) -> int:
    """Write down what was already connected, so nothing stops working.

    Anything local that was connected while the answer lived in an
    environment variable has an implied grant: the person allowed it, in the
    only way Bevro offered at the time. It is recorded as its own grant so
    that Settings can show it and they can take it back - which is more than
    they could do before.

    Runs where the filesystem is. In the API, where a container cannot see
    the host, paths cannot be canonicalised and nothing is adopted.
    """
    from app.models import Provider

    adopted = 0
    for provider in db.execute(select(Provider)).scalars():
        source = provider.source or {}
        kind = str(source.get("target_kind") or source.get("kind") or "")
        target = str(source.get("target") or "")
        if not target or kind not in ("local", "command"):
            continue
        try:
            if kind == "local":
                path = canonical(target)
                if refuse_reason(path) or _already(db, FOLDER, str(path)):
                    continue
                grant(db, FOLDER, str(path), scope="exact", granted_by="migration")
            else:
                program = target.split(" ", 1)[0]
                if _already(db, COMMAND, program):
                    continue
                grant(db, COMMAND, program, granted_by="migration")
        except TrustError:
            continue  # not here any more, or not somewhere Bevro may go
        adopted += 1
    if adopted:
        db.commit()
        log.info("recorded %s existing permission(s) so they can be seen and taken back", adopted)
    return adopted


def _already(db: Session, kind: str, target: str) -> bool:
    return db.execute(select(TrustGrant).where(TrustGrant.kind == kind, TrustGrant.target == target)).scalar_one_or_none() is not None
