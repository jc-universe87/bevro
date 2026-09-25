"""Safe, read-only looks at a local project.

Every read goes through here so the rules live in one place: stay inside the
project, follow no symlink out of it, read only small text files, never
open .env or anything that looks like a credential, never execute anything.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MAX_TEXT_BYTES = 64_000
MAX_ENTRIES = 400
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".next", ".cache", ".tox", ".idea", ".vscode"})
# Never read, regardless of what asks. Names only; contents stay unknown.
FORBIDDEN_NAMES = re.compile(r"^(\.env(\..*)?|.*\.(pem|key|p12|pfx|crt|cer)|credentials(\..*)?|secrets?(\..*)?|id_(rsa|ed25519|ecdsa|dsa)(\.pub)?|\.netrc|\.npmrc|\.pypirc)$", re.IGNORECASE)
_ENV_EXAMPLE = re.compile(r"^\.env\.(example|sample|template|dist)$", re.IGNORECASE)


class Project:
    """A resolved project directory and the readers allowed on it."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @property
    def name(self) -> str:
        return self.root.name

    def _inside(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved == self.root or self.root in resolved.parents:
            return resolved
        return None  # a symlink pointing out of the project

    def exists(self, rel: str) -> bool:
        return self._inside(self.root / rel) is not None

    def is_dir(self, rel: str) -> bool:
        p = self._inside(self.root / rel)
        return p is not None and p.is_dir()

    def is_file(self, rel: str) -> bool:
        p = self._inside(self.root / rel)
        return p is not None and p.is_file()

    def is_executable(self, rel: str) -> bool:
        p = self._inside(self.root / rel)
        return p is not None and p.is_file() and os.access(p, os.X_OK)

    def venv_program(self, rel: str) -> str | None:
        """A program under the project's own virtual environment, e.g. ".venv/bin/python".

        Such files are symlinks to an interpreter *outside* the project by
        design, so the symlink-escape rule does not apply; the path is only
        ever used as argv[0], never read.
        """
        if ".." in Path(rel).parts or not rel.startswith((".venv/", "venv/")):
            return None
        path = self.root / rel
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None

    def read_text(self, rel: str, limit: int = MAX_TEXT_BYTES) -> str | None:
        """Small text files only. Credentials-looking names are never opened."""
        name = Path(rel).name
        if FORBIDDEN_NAMES.match(name) and not _ENV_EXAMPLE.match(name):
            return None
        path = self._inside(self.root / rel)
        if path is None or not path.is_file():
            return None
        try:
            if path.stat().st_size > limit:
                with path.open("rb") as fh:
                    raw = fh.read(limit)
            else:
                raw = path.read_bytes()
        except OSError:
            return None
        if b"\0" in raw[:4000]:
            return None  # binary
        return raw.decode("utf-8", errors="replace")

    def listdir(self, rel: str = ".") -> list[str]:
        path = self._inside(self.root / rel)
        if path is None or not path.is_dir():
            return []
        try:
            names = sorted(os.listdir(path))
        except OSError:
            return []
        return [n for n in names if n not in SKIP_DIRS][:MAX_ENTRIES]

    def declares_env_var(self, name: str) -> bool:
        """Does the project's own .env define this variable *name*?

        The one, narrow look at a credential file: a line-by-line scan for
        `NAME=`. The value is never read into memory beyond the line being
        matched, never kept, never returned. Used only to say "uses its own
        credential" instead of asking the person for one.
        """
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
            return False
        pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*\S")
        for env_name in (".env", ".env.local"):
            path = self._inside(self.root / env_name)
            if path is None or not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if pattern.match(line):
                            return True
            except OSError:
                continue
        return False

    def env_port(self, name: str) -> tuple[str, int | None]:
        """What the project's .env sets a Compose port variable to, as a port only.

        The second narrow look at a credential file, for the one thing Compose
        itself reads it for: filling in `${NAME}` in a port. The answer is a
        state - "unset", "empty", "port" or "other" - and the number when it
        is a port. Any other value is dropped where it is read: never kept,
        never returned, never logged. Only `.env`, because that is the file
        Compose reads for this.
        """
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            return "unset", None
        pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(name) + r"\s*=(.*)$")
        path = self._inside(self.root / ".env")
        if path is None or not path.is_file():
            return "unset", None
        state: tuple[str, int | None] = ("unset", None)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = pattern.match(line.rstrip("\n"))
                    if m is None:
                        continue
                    value = m.group(1).split(" #", 1)[0].strip().strip("\"'")
                    # The last assignment wins, as it does for Compose.
                    if not value:
                        state = ("empty", None)
                    elif value.isdigit() and 0 < int(value) < 65536:
                        state = ("port", int(value))
                    else:
                        state = ("other", None)
        except OSError:
            return "unset", None
        return state

    def env_example_keys(self) -> list[str]:
        """Variable names (only names) from .env.example-style files."""
        keys: list[str] = []
        for name in self.listdir():
            if _ENV_EXAMPLE.match(name):
                text = self.read_text(name, 16_000) or ""
                for line in text.splitlines():
                    m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})\s*=", line)
                    if m and m.group(1) not in keys:
                        keys.append(m.group(1))
        return keys[:20]


def readme_body(project: Project, limit: int = 32_000) -> str:
    """The README text itself, bounded. Used only for low-weight capability hints."""
    for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
        text = project.read_text(name, limit)
        if text is not None:
            return text
    return ""


def readme_excerpt(project: Project, limit: int = 600) -> tuple[str | None, str | None]:
    """(title, first descriptive paragraph) from a README, if any."""
    for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
        text = project.read_text(name, 32_000)
        if text is None:
            continue
        title = None
        paragraphs: list[str] = []
        buf: list[str] = []
        in_fence = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                if buf:
                    paragraphs.append(" ".join(buf))
                    buf = []
                continue
            if in_fence:
                continue
            if title is None and stripped.startswith("# "):
                title = stripped[2:].strip()
                continue
            if stripped.startswith(("#", "<", "![", "[!", "|", "---", "===")) or not stripped:
                if buf:
                    paragraphs.append(" ".join(buf))
                    buf = []
                continue
            buf.append(stripped)
        if buf:
            paragraphs.append(" ".join(buf))
        prose = next((p for p in paragraphs if len(p) > 20 and not p.startswith(("-", "*", ">"))), None)
        if prose and len(prose) > limit:
            prose = prose[:limit].rsplit(" ", 1)[0] + "…"
        return title, prose
    return None, None


def humanise(slug: str) -> str:
    words = re.split(r"[-_.\s]+", slug.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w)[:80] or slug[:80]
