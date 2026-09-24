"""Approved local roots: the only directories Bevro may inspect or run things in.

Where the list comes from is the process's business, not this module's. The
worker sets it from the trust grants the person made, refreshing it before it
looks at or runs anything, which is what makes revoking one take effect
without a restart. Where nothing has been set it falls back to
BEVRO_LOCAL_ROOTS, which is how a hardened installation pins the boundary and
how tests set one up.

Nothing in a browser request reaches this. A path is accepted only if, after
resolving symlinks, it sits inside one of the roots.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "BEVRO_LOCAL_ROOTS"
# Directories Bevro made and owns: an integration bridge it had built, an agent
# it was asked to create. They are approved by their nature - the person never
# has to list their own home directory to let Bevro run something it wrote
# itself - while everything of the person's still requires BEVRO_LOCAL_ROOTS.
MANAGED_ENV_VARS = ("BEVRO_INTEGRATIONS_DIR", "BEVRO_AGENTS_DIR")


class OutsideRoots(Exception):
    """The path is not inside any approved root (or no roots are configured)."""


def parse_roots(raw: str | None) -> list[Path]:
    """Split a ':'- or ','-separated list, expand ~, drop blanks and non-directories."""
    roots: list[Path] = []
    for part in (raw or "").replace(",", ":").split(":"):
        part = part.strip()
        if not part:
            continue
        candidate = Path(os.path.expanduser(part))
        if candidate.is_absolute() and candidate.is_dir():
            roots.append(candidate.resolve())
    return roots


def managed_roots() -> list[Path]:
    """Bevro's own folders, always approved."""
    return [root for name in MANAGED_ENV_VARS for root in parse_roots(os.environ.get(name))]


# What this process has been told it may touch. None means "nobody has said",
# and the environment answers instead.
_effective: list[Path] | None = None


def set_effective_roots(roots: list[Path] | None) -> None:
    """What this process may touch, as decided by whoever knows.

    The worker calls this from the trust grants before each piece of work.
    Passing None hands the question back to the environment.
    """
    global _effective
    _effective = None if roots is None else list(roots)


def effective_roots() -> list[Path] | None:
    return None if _effective is None else list(_effective)


def configured_roots() -> list[Path]:
    """Everywhere Bevro may look: what it was told, plus its own folders."""
    roots = list(_effective) if _effective is not None else parse_roots(os.environ.get(ENV_VAR))
    for root in managed_roots():
        if root not in roots:
            roots.append(root)
    return roots


def resolve_within(raw: str, roots: list[Path] | None = None) -> Path:
    """Resolve `raw` (with ~ expansion) and prove it lies inside an approved root.

    Symlinks are followed before the check, so a link that points out of a
    root is rejected. Relative paths are rejected outright.
    """
    roots = configured_roots() if roots is None else roots
    if not roots:
        raise OutsideRoots("no local roots are configured")
    if not raw or not isinstance(raw, str) or "\0" in raw:
        raise OutsideRoots("empty path")
    path = Path(os.path.expanduser(raw.strip()))
    if not path.is_absolute():
        raise OutsideRoots("path must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise OutsideRoots(f"path does not exist: {path.name}") from exc
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise OutsideRoots("path is outside the approved local roots")


def find_by_name(name: str, roots: list[Path] | None = None) -> Path | None:
    """A bare folder name typed on Connect: look for it directly under each root."""
    roots = configured_roots() if roots is None else roots
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    for root in roots:
        candidate = root / name
        if candidate.is_dir():
            try:
                return resolve_within(str(candidate), roots)
            except OutsideRoots:
                continue
    return None
