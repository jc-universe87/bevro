"""Finding something on this machine by what it is called.

"inventory-agent", "Research Agent", "my_reporting_agent": a person who knows
what a project is called should not also have to know where it lives. This
turns a name into the folder it names - and nothing more. What the folder
*is* is discovery's question, asked afterwards, and only once the person has
allowed Bevro to look inside it.

Finding a folder's name is not looking inside a project. The search reads
directory entries - names, and whether each is a directory - and nothing
else: no README, no pyproject.toml, not one byte of any file.

It is bounded every way it can be:

    where       the folders the person already allowed, the folders their
                connected projects sit in, the administrator's roots, and
                this user's own home - each to a fixed depth
    what        hidden folders, dependency and build trees, the operating
                system's folders, credential folders and network mounts are
                never entered
    links       a link is never followed down; a link whose name matches is
                resolved, and kept only if it stays inside a search area
    how much    a cap on directories visited, a time limit, and a cap on
                what is returned

It runs on the worker, which is where the filesystem is. The API never
walks a folder.
"""

from __future__ import annotations

import os
import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Folders that are never a project someone would name, and are often huge.
SKIP = frozenset(
    {
        "node_modules", "bower_components", "vendor", "site-packages", "dist-packages", "__pycache__",
        "venv", "virtualenv", "target", "Trash", "snap", "Library", "Applications",
    }
)
# Filesystems that live on another machine. Walking one is walking someone
# else's disk, slowly.
NETWORK_FILESYSTEMS = frozenset(
    {
        "nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "afs", "ceph", "glusterfs", "lustre", "davfs",
        "fuse.sshfs", "sshfs", "fuse.rclone", "fuse.s3fs", "fuse.gcsfuse", "fuse.gvfsd-fuse", "fuse.davfs2",
    }
)
MAX_DIRECTORIES = 25_000
TIME_LIMIT_SECONDS = 8.0  # a cold disk cache, not a slow search: warm, it takes a tenth of a second
MAX_CANDIDATES = 5
MAX_NAME_LENGTH = 120


@dataclass(frozen=True)
class SearchArea:
    """A folder to look below, and how far."""

    path: Path
    depth: int


def normalise(text: str) -> str:
    """"Research Agent", "research-agent" and "research_agent" are one name."""
    return re.sub(r"[\s._-]+", "-", text.strip().lower()).strip("-")


def network_mounts(mounts_file: str = "/proc/self/mounts") -> frozenset[str]:
    """Where this machine has something else's disk mounted."""
    found: set[str] = set()
    try:
        with open(mounts_file, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3 and parts[2] in NETWORK_FILESYSTEMS:
                    # /proc/mounts writes a space in a path as \040.
                    found.add(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return frozenset(found)


def find_named(
    name: str,
    areas: list[SearchArea],
    *,
    refuse: Callable[[Path], str | None],
    mounts: frozenset[str] | None = None,
    max_directories: int = MAX_DIRECTORIES,
    time_limit: float = TIME_LIMIT_SECONDS,
) -> list[Path]:
    """Canonical folders called `name`, most likely first.

    `refuse` says why a path may not be used at all (the operating system's
    folders, credential folders, outside an administrator's boundary);
    nothing it refuses is returned or entered. Areas are searched in the
    order given, breadth first, in name order, so the answer is the same
    every time for the same machine.
    """
    wanted = normalise(name)
    if not wanted or len(name) > MAX_NAME_LENGTH:
        return []
    mounts = network_mounts() if mounts is None else mounts
    deadline = time.monotonic() + time_limit
    real_areas = [(area, _real(area.path)) for area in areas]
    permitted = [real for _area, real in real_areas if real is not None]
    found: dict[Path, None] = {}
    visited = 0

    def keep(path: Path) -> None:
        real = _real(path)
        if real is None or real in found or not real.is_dir():
            return
        # A link that leads out of every place Bevro was looking leads
        # somewhere nobody asked it to look.
        if not any(real == base or base in real.parents for base in permitted):
            return
        if refuse(real) is not None:
            return
        # A project's own package is usually called what the project is
        # ("research-agent/src/research_agent"). A match inside a match is
        # part of the same thing, and the outermost is the project.
        if any(other in real.parents for other in found):
            return
        for inner in [other for other in found if real in other.parents]:
            del found[inner]
        found[real] = None

    for area, real_root in real_areas:
        if real_root is None or refuse(real_root) is not None:
            continue
        if normalise(real_root.name) == wanted:
            keep(real_root)
        queue: deque[tuple[Path, int]] = deque([(real_root, 0)])
        while queue:
            folder, depth = queue.popleft()
            if depth >= area.depth:
                continue
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name)
            except OSError:
                continue  # not ours to read, or gone
            for entry in entries:
                visited += 1
                if visited > max_directories or time.monotonic() > deadline:
                    return list(found)[:MAX_CANDIDATES]
                if entry.name.startswith(".") or entry.name in SKIP:
                    continue
                try:
                    is_link = entry.is_symlink()
                    is_dir = entry.is_dir(follow_symlinks=True)
                except OSError:
                    continue
                if not is_dir:
                    continue
                path = Path(entry.path)
                if normalise(entry.name) == wanted:
                    keep(path)
                # Down only through real folders, on this machine's own disks,
                # that Bevro could ever be allowed to use.
                if is_link or entry.path in mounts or refuse(path) is not None:
                    continue
                queue.append((path, depth + 1))
            if len(found) >= MAX_CANDIDATES:
                return list(found)
    return list(found)


def _real(path: Path) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def shown(path: Path) -> str:
    """How a candidate is written for the person: under their home as ~/..."""
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home)) if path != home else "~"
    except ValueError:
        return str(path)
