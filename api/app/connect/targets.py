"""What did the person type? A URL, a folder, an MCP server or a command.

Classification is pure text handling: nothing is fetched, opened or run.
Commands are split without a shell and refused if they contain anything a
shell would interpret, so a target can never smuggle in a second command.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

TargetKind = Literal["url", "mcp", "local", "command"]

MAX_TARGET_LENGTH = 2000
# Anything a shell would treat specially. Quotes are allowed (shlex handles them).
_SHELL_META = re.compile(r"[;|&<>`$(){}\n\r\\]")
# Programs that commonly launch an agent. Others are accepted with a warning.
KNOWN_LAUNCHERS = frozenset(
    {"python", "python3", "node", "npx", "npm", "pnpm", "yarn", "bun", "deno", "uv", "uvx", "pipx", "docker", "java", "go", "cargo", "ruby", "php", "dotnet"}
)
_PY_VERSIONED = re.compile(r"^python3\.\d+$")
_MCP_HINT = re.compile(r"/(mcp|sse)/?$", re.IGNORECASE)
_PATHLIKE = re.compile(r"^(~|/|\./|\.\./)")
_BARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class TargetError(ValueError):
    """The input cannot be understood or is refused. The message is for the person."""


@dataclass(frozen=True)
class ConnectTarget:
    kind: TargetKind
    value: str                              # URL, path text, or the command text as typed
    argv: tuple[str, ...] = ()              # commands only: the parsed words
    hints: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """What the browser may see: never an absolute path."""
        if self.kind == "local":
            name = self.value.rstrip("/").rsplit("/", 1)[-1]
            return name or self.value
        return self.value


def split_command(text: str) -> list[str]:
    """shlex without a shell. Refuses shell metacharacters outright."""
    if _SHELL_META.search(text):
        raise TargetError("That looks like a shell command with pipes or redirections. Enter one program and its arguments only.")
    try:
        words = shlex.split(text, posix=True)
    except ValueError as exc:
        raise TargetError("The command has unbalanced quotes.") from exc
    if not words:
        raise TargetError("Enter a command.")
    for word in words:
        if "\0" in word:
            raise TargetError("The command contains an invalid character.")
    return words


def is_known_launcher(program: str) -> bool:
    name = program.rsplit("/", 1)[-1]
    return name in KNOWN_LAUNCHERS or bool(_PY_VERSIONED.match(name))


def classify_target(raw: str) -> ConnectTarget:
    text = (raw or "").strip()
    if not text:
        raise TargetError("Enter a web address, a folder on this machine, an MCP server or a command.")
    if len(text) > MAX_TARGET_LENGTH:
        raise TargetError("That is too long to be an address, a folder or a command.")
    if "\0" in text or "\n" in text or "\r" in text:
        raise TargetError("The input contains an invalid character.")

    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        parts = urlsplit(text)
        if not parts.netloc:
            raise TargetError("That web address is missing its host.")
        kind: TargetKind = "mcp" if _MCP_HINT.search(parts.path or "") else "url"
        return ConnectTarget(kind=kind, value=text, hints=("mcp",) if kind == "mcp" else ())

    if " " in text or "\t" in text:
        # A path with spaces must be quoted; otherwise this is a command.
        if text.startswith(('"', "'")):
            words = split_command(text)
            if len(words) == 1 and _PATHLIKE.match(words[0]):
                return ConnectTarget(kind="local", value=words[0])
        words = split_command(text)
        return ConnectTarget(kind="command", value=text, argv=tuple(words))

    if _PATHLIKE.match(text):
        return ConnectTarget(kind="local", value=text)

    if _BARE_NAME.match(text):
        # "my-agent": a folder name under an approved root, or a program.
        hints = ("bare",)
        return ConnectTarget(kind="local", value=text, hints=hints)

    if "/" in text and not text.startswith("-"):
        # "agents/foo" style relative path
        return ConnectTarget(kind="local", value=text, hints=("relative",))

    raise TargetError("Enter a web address, a folder on this machine, an MCP server or a command.")
