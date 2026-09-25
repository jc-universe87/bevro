"""Which host port a Compose service publishes, read the way Compose reads it.

A `ports:` entry is `[HOST_IP:][HOST_PORT:]CONTAINER_PORT[/PROTOCOL]`, and
any part of it may be a variable: `127.0.0.1:${WEB_PORT:-8080}:80`. Splitting
that on ":" cuts the variable in half, so the entry is split only at colons
outside `${...}` and `[...]`, and then only the host-port part is worked out.

Variables are filled in as Compose fills them in, from the project's `.env`,
with Compose's defaults (`:-`, `-`), required markers (`:?`, `?`) and
alternatives (`:+`, `+`). The `.env` is asked about one variable at a time,
and answers with a port number or nothing (see `Project.env_port`): whatever
else it holds never reaches this module. The environment of the process
doing the discovery is deliberately not used: it is not the environment the
application was started from, and it carries Bevro's own secrets.

Anything that cannot be worked out with confidence - a malformed variable, a
required one that is missing, a range, a value that is not a port - is None:
"a port Bevro does not know", never a guess.
"""

from __future__ import annotations

import re
from typing import Any, Callable

# One variable, the whole of the text: ${NAME}, ${NAME<op><word>}, $NAME.
_BRACED = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])(.*))?\}$", re.S)
_BARE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")

# (state, port): see Project.env_port.
Lookup = Callable[[str], tuple[str, "int | None"]]


def _no_variables(_name: str) -> tuple[str, int | None]:
    return "unset", None


def split_entry(entry: str) -> list[str] | None:
    """The colon-separated parts, keeping `${...}` and `[...]` whole. None if unbalanced."""
    parts: list[str] = []
    current: list[str] = []
    depth_brace = depth_bracket = 0
    i = 0
    while i < len(entry):
        ch = entry[i]
        if ch == "$" and entry[i : i + 2] == "${":
            depth_brace += 1
            current.append("${")
            i += 2
            continue
        if ch == "}" and depth_brace:
            depth_brace -= 1
        elif ch == "[" and not depth_brace:
            depth_bracket += 1
        elif ch == "]" and depth_bracket and not depth_brace:
            depth_bracket -= 1
        elif ch == ":" and not depth_brace and not depth_bracket:
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if depth_brace or depth_bracket:
        return None
    parts.append("".join(current))
    return parts


def _port(text: str) -> int | None:
    text = text.strip()
    if text.isdigit() and 0 < int(text) < 65536:
        return int(text)
    return None


def resolve_port(text: str, lookup: Lookup = _no_variables, _depth: int = 0) -> int | None:
    """A host-port value - a number or one variable expression - as a port, or None."""
    text = str(text).strip()
    if _depth > 3:
        return None
    if "$" not in text:
        return _port(text)
    if text.startswith("$$"):
        return None  # an escaped dollar: a literal "$...", which is not a port
    bare = _BARE.match(text)
    if bare:
        state, port = lookup(bare.group(1))
        return port if state == "port" else None
    braced = _BRACED.match(text)
    if not braced:
        return None  # malformed, or several pieces glued together
    name, op, word = braced.group(1), braced.group(2), braced.group(3) or ""
    state, port = lookup(name)
    unset = state == "unset"
    empty_or_unset = state in ("unset", "empty")
    if op is None:
        return port if state == "port" else None
    if op in (":-", "-"):
        missing = empty_or_unset if op == ":-" else unset
        if missing:
            return resolve_port(word, lookup, _depth + 1)
        return port if state == "port" else None
    if op in (":?", "?"):
        # Compose refuses to start without it; if it is there, it is used.
        return port if state == "port" else None
    if op in (":+", "+"):
        present = not empty_or_unset if op == ":+" else not unset
        return resolve_port(word, lookup, _depth + 1) if present else None
    return None


def published_port(entry: Any, lookup: Lookup = _no_variables) -> int | None:
    """The host port one `ports:` entry publishes, or None when it cannot be known.

    Short syntax: "8080:80", "127.0.0.1:8080:80", "${PORT:-8080}:80",
    "[::1]:8080:80", with or without "/tcp". A lone "8000" is read as 8000,
    as it always has been here. Long syntax: {"published": ..., "target": ...}.
    """
    if isinstance(entry, bool):
        return None
    if isinstance(entry, int):
        return entry if 0 < entry < 65536 else None
    if isinstance(entry, dict):
        published = entry.get("published")
        if published is None or published == "":
            target = entry.get("target")
            return resolve_port(str(target), lookup) if target is not None else None
        return resolve_port(str(published), lookup)
    if not isinstance(entry, str):
        return None
    parts = split_entry(entry.strip())
    if parts is None or not parts:
        return None
    container = parts[-1].split("/", 1)[0]
    if len(parts) == 1:
        return resolve_port(container, lookup)
    if len(parts) > 3:
        return None
    host = parts[-2]
    if not host.strip():
        return None  # "127.0.0.1::80": Docker picks a port, and it isn't written down
    if "-" in host and "$" not in host:
        return None  # a range
    return resolve_port(host, lookup)


def bind_scope(entry: Any) -> str | None:
    """Where one `ports:` entry publishes: "all", "loopback", "address", or None.

    Compose publishes on every interface unless an address is written in
    front, so "8080:80" is "all". An address that comes from a variable
    cannot be known here and says None - which is treated as loopback by
    anything deciding whether a browser elsewhere could open it.
    """
    if isinstance(entry, dict):
        value = entry.get("host_ip")
        if value is None:
            return "all"
    elif isinstance(entry, int):
        return "all"
    elif isinstance(entry, str):
        parts = split_entry(entry.strip())
        if not parts:
            return None
        if len(parts) < 3:
            return "all"
        value = parts[0]
    else:
        return None
    if not isinstance(value, str) or "$" in value:
        return None
    value = value.strip().strip("[]")
    if value in ("", "0.0.0.0", "::"):
        return "all"
    if value in ("localhost", "::1") or value.startswith("127."):
        return "loopback"
    return "address"


def host_address(entry: Any) -> str | None:
    """The address a port is published on, when it is written literally and is not a wildcard."""
    if isinstance(entry, dict):
        value = entry.get("host_ip")
    elif isinstance(entry, str):
        parts = split_entry(entry.strip())
        value = parts[0] if parts and len(parts) == 3 else None
    else:
        value = None
    if not isinstance(value, str) or "$" in value:
        return None
    value = value.strip().strip("[]")
    if value in ("", "0.0.0.0", "::"):
        return None
    return value
