"""Workspace inspection shared by coding providers.

Answers three questions about an approved directory, before and after a run:
what does git say (branch, HEAD, dirty files), which files changed *during*
the run as opposed to being dirty already, and what is the diff of the files
the run touched. Works without git too, by snapshotting file sizes and
modification times.

Nothing here copies a repository anywhere.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IGNORED_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".next", ".cache", ".tox"}
)
MAX_FILES = 60_000
MAX_HASHED_DIRTY = 500
MAX_DIFF_BYTES = 200_000


class WorkspaceUnavailable(Exception):
    pass


def validate_workspace_path(raw: str) -> Path:
    """The path must be absolute, exist and be a directory. `~` is expanded.

    This is the only place a path is turned into something a provider runs
    against; the value itself always comes from server-side configuration.
    """
    if not raw or not isinstance(raw, str):
        raise WorkspaceUnavailable("no workspace path configured")
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        raise WorkspaceUnavailable("workspace path must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise WorkspaceUnavailable(f"workspace path does not exist: {path}") from exc
    if not resolved.is_dir():
        raise WorkspaceUnavailable(f"workspace path is not a directory: {resolved}")
    return resolved


def _git(path: Path, *args: str, timeout: float = 30.0) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=path, capture_output=True, text=True, timeout=timeout, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def is_git_repo(path: Path) -> bool:
    return _git(path, "rev-parse", "--is-inside-work-tree") is not None


@dataclass
class GitState:
    branch: str | None = None
    head: str | None = None
    # path -> two-letter porcelain status, e.g. " M", "??", "A "
    status: dict[str, str] = field(default_factory=dict)
    # content hashes of dirty files, so a file dirty before *and* after can be attributed properly
    hashes: dict[str, str] = field(default_factory=dict)

    @property
    def dirty(self) -> bool:
        return bool(self.status)

    def to_meta(self) -> dict[str, Any]:
        return {"branch": self.branch, "head": self.head, "dirty": self.dirty, "dirty_files": sorted(self.status)}


def _hash_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def git_state(path: Path) -> GitState:
    state = GitState()
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    state.branch = branch.strip() if branch else None
    head = _git(path, "rev-parse", "HEAD")
    state.head = head.strip() if head else None
    porcelain = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if porcelain:
        for entry in porcelain.split("\0"):
            if len(entry) < 4:
                continue
            code, rel = entry[:2], entry[3:]
            state.status[rel] = code
    for i, rel in enumerate(sorted(state.status)):
        if i >= MAX_HASHED_DIRTY:
            break
        if state.status[rel][1] != "D" and state.status[rel] != "D ":
            digest = _hash_file(path / rel)
            if digest:
                state.hashes[rel] = digest
    return state


def snapshot(path: Path, exclude: list[Path] | None = None) -> dict[str, tuple[int, int]]:
    """rel path -> (size, mtime_ns) for every file, skipping noisy directories."""
    excluded = [p.resolve() for p in (exclude or [])]
    snap: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(path):
        root_path = Path(root)
        dirs[:] = [
            d for d in dirs
            if d not in IGNORED_DIRS and not any((root_path / d).resolve() == e or e in (root_path / d).resolve().parents for e in excluded)
        ]
        for name in files:
            full = root_path / name
            try:
                st = full.stat()
            except OSError:
                continue
            snap[str(full.relative_to(path))] = (st.st_size, st.st_mtime_ns)
            if len(snap) >= MAX_FILES:
                return snap
    return snap


@dataclass
class Changes:
    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    # dirty before the run and untouched by it
    pre_existing: list[str] = field(default_factory=list)

    @property
    def touched(self) -> list[str]:
        return sorted(set(self.created) | set(self.modified) | set(self.deleted))

    @property
    def count(self) -> int:
        return len(self.touched)

    def to_rows(self) -> list[list[str]]:
        rows = [[p, "Created"] for p in sorted(self.created)]
        rows += [[p, "Modified"] for p in sorted(self.modified)]
        rows += [[p, "Deleted"] for p in sorted(self.deleted)]
        return rows


def changes_from_git(before: GitState, after: GitState) -> Changes:
    changes = Changes()
    for rel, code in after.status.items():
        prev = before.status.get(rel)
        deleted = code[1] == "D" or code == "D "
        untracked_or_added = code == "??" or code[0] == "A"
        if prev is None:
            (changes.deleted if deleted else changes.created if untracked_or_added else changes.modified).append(rel)
        elif deleted and not (prev[1] == "D" or prev == "D "):
            changes.deleted.append(rel)
        else:
            # Dirty before and after: only count it if the content changed during the run.
            if before.hashes.get(rel) != after.hashes.get(rel) and rel in after.hashes:
                changes.modified.append(rel)
            else:
                changes.pre_existing.append(rel)
    for rel in before.status:
        if rel not in after.status:
            # Was dirty, now clean: reverted or committed during the run. Count as modified.
            changes.modified.append(rel)
    return changes


def changes_from_snapshots(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> Changes:
    changes = Changes()
    for rel, sig in after.items():
        if rel not in before:
            changes.created.append(rel)
        elif before[rel] != sig:
            changes.modified.append(rel)
    changes.deleted = [rel for rel in before if rel not in after]
    return changes


def git_diff(path: Path, changes: Changes) -> str | None:
    """Unified diff for what the run touched. Truncated past MAX_DIFF_BYTES."""
    parts: list[str] = []
    tracked = [p for p in sorted(set(changes.modified) | set(changes.deleted))]
    if tracked:
        out = _git(path, "diff", "--no-color", "--", *tracked)
        if out:
            parts.append(out)
    for rel in sorted(changes.created):
        full = path / rel
        if not full.is_file():
            continue
        try:
            out = subprocess.run(
                ["git", "diff", "--no-color", "--no-index", "--", "/dev/null", rel], cwd=path, capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.stdout:
            parts.append(out.stdout)
    if not parts:
        return None
    text = "\n".join(parts)
    if len(text.encode("utf-8")) > MAX_DIFF_BYTES:
        text = text.encode("utf-8")[:MAX_DIFF_BYTES].decode("utf-8", "ignore") + "\n\n[diff truncated]\n"
    return text
