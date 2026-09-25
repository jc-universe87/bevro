"""Execution contexts: what launches a program, and what that launch hands it.

A project's command line run from a shell and the same command line started
by its installed service are one program with two different environments.
Only the second may have the key. This module records the second kind - a
systemd unit, a socket that starts one, a Compose service a one-off could be
run from - as evidence, and says whether it could ever carry Bevro's work:

    does it belong to this project       its working folder or program is here
    does it run a program Bevro found    the same interpreter and module, or
                                         the same executable
    is that program fixed                a shell, or an interpreter that would
                                         evaluate its arguments, is not
    can a request reach it               stdin on a socket, or values after a
                                         fixed program - never a short name,
                                         never nothing
    may this user use it                 without administrator permission

Nothing here runs anything, and nothing here reads a secret: the credential
names a context supplies come from the runtime's credential evidence, which
is built from configuration alone.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from adapters.runtime import ContextInput, ContextKind, Credentials, ExecutionContext, RuntimeKind, RuntimeProfile
from app.connect import probes
from app.connect.probes import SystemdUnit

SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ash", "ksh", "fish", "busybox"})
INTERPRETER = re.compile(r"^(python[0-9.]*|node|nodejs|ruby|perl|php|deno|bun)$")
# Options after which an interpreter runs its *argument* as code.
EVALUATES = frozenset({"-c", "-e", "--eval", "-p", "--print", "-r", "-"})
# The prefixes systemd allows in front of an ExecStart command.
EXEC_PREFIX = "@-:+!|"
SPECIFIER = re.compile(r"%[iIjJ]")

# Ways in a context could carry: programs Bevro starts itself.
WORK_INTERFACES = frozenset({RuntimeKind.CLI, RuntimeKind.PYTHON_ENTRYPOINT, RuntimeKind.NODE_ENTRYPOINT, RuntimeKind.MCP_STDIO})


# --------------------------------------------------------------------------- programs

def _without_env(argv: list[str]) -> list[str]:
    """`env NAME=value program ...` is `program ...` for these purposes."""
    if argv and os.path.basename(argv[0]) == "env":
        rest = argv[1:]
        while rest and ("=" in rest[0] or rest[0].startswith("-")):
            rest = rest[1:]
        return rest
    return list(argv)


def program_is_fixed(argv: list[str]) -> bool:
    """Does this command decide for itself what runs, whatever follows it?

    `python -m pkg.agent` does: anything after it is data to that module.
    `sh -c`, `python -c`, `node -e`, or an interpreter with nothing after
    it, does not: what follows is code, and handing it a request would be
    handing it a program.
    """
    argv = _without_env(argv)
    if not argv:
        return False
    head = os.path.basename(argv[0])
    if head in SHELLS:
        return False
    if INTERPRETER.match(head):
        for token in argv[1:]:
            if token in EVALUATES:
                return False
            if token == "-m" or not token.startswith("-"):
                return True
        return False  # an interpreter and its options, and nothing to run
    return True


def _program_part(argv: list[str]) -> tuple[str, list[str]]:
    """(what runs, what follows): the interpreter's own options set aside."""
    argv = _without_env(argv)
    head = os.path.basename(argv[0])
    if INTERPRETER.match(head):
        rest = argv[1:]
        while rest and rest[0].startswith("-") and rest[0] != "-m":
            rest = rest[1:]
        return re.sub(r"[0-9.]+$", "", head), rest
    return head, argv[1:]


def _same_token(a: str, b: str) -> bool:
    if a == b:
        return True
    # A script named by path: the same file, whether the path is the
    # container's, the host's, or relative to where it is run.
    if "/" in a or "/" in b:
        pa, pb = Path(a).parts, Path(b).parts
        shorter, longer = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
        return bool(shorter) and longer[-len(shorter):] == shorter
    return False


def same_program(context_argv: list[str], entry_argv: list[str], *, host: bool) -> bool:
    """Is what the context runs the program discovery found?

    The entry point's words must begin the context's command; the context
    may add fixed words of its own after them (a subcommand, say). On this
    machine the interpreter must be the very same one - another Python has
    other packages, and is another program - while in a container only its
    family can be compared.
    """
    a, b = _without_env(context_argv), _without_env(entry_argv)
    if not a or not b:
        return False
    head_a, rest_a = _program_part(a)
    head_b, rest_b = _program_part(b)
    if head_a != head_b:
        return False
    if host and ("/" in a[0] or "/" in b[0]):
        if _real_program(a[0]) != _real_program(b[0]):
            return False
    if not rest_b or len(rest_a) < len(rest_b):
        return False
    return all(_same_token(x, y) for x, y in zip(rest_a, rest_b))


def _real_program(exe: str) -> str:
    """The file a program name really is: looked up on PATH if it is a bare
    name, then with symlinks followed (python3 -> python3.12)."""
    found = exe if "/" in exe else (shutil.which(exe) or exe)
    return os.path.realpath(found)


def _exec_argv(exec_start: str | None) -> tuple[list[str], bool]:
    """(the command, whether it takes a template's instance name)."""
    if not exec_start:
        return [], False
    value = exec_start.lstrip(EXEC_PREFIX).strip()
    try:
        argv = shlex.split(value, posix=True)
    except ValueError:
        return [], False
    return argv, bool(SPECIFIER.search(value))


def _inside(path: str | None, root: Path) -> bool:
    if not path or not path.startswith("/"):
        return False
    try:
        resolved = Path(os.path.realpath(path))
    except OSError:
        return False
    root = Path(os.path.realpath(root))
    return resolved == root or root in resolved.parents


# --------------------------------------------------------------------------- systemd

def systemd_context(unit: SystemdUnit, owner: Path, credentials: Credentials) -> ExecutionContext:
    """A unit, as somewhere a program could be launched with its environment."""
    program, instance = _exec_argv(unit.exec_start)
    socket = unit.socket
    takes_connections = socket is not None and socket.accept and (unit.standard_input or "").startswith("socket")
    if takes_connections:
        kind, channel = ContextKind.SYSTEMD_SOCKET, ContextInput.STDIN
    else:
        kind = ContextKind.SYSTEMD_SERVICE
        channel = ContextInput.INSTANCE_NAME if unit.template and instance else ContextInput.NONE

    available = unit.installed and (not takes_connections or socket.installed)
    problems: list[str] = []
    if not available:
        problems.append("not_installed")
    if channel == ContextInput.NONE:
        problems.append("no_request_channel")
    elif channel == ContextInput.INSTANCE_NAME:
        problems.append("instance_name_only")
    if not program:
        problems.append("program_unknown")
    elif not program_is_fixed(program):
        problems.append("program_not_fixed")
    if not (_inside(unit.working_directory, owner) or any(_inside(word, owner) for word in program[:2])):
        problems.append("different_owner")

    privilege = _systemd_privilege(unit, takes_connections)
    if privilege == "needs_admin":
        problems.append("needs_admin")
    elif privilege == "unknown" and available:
        problems.append("permission_unknown")
    return ExecutionContext(
        kind=kind,
        source_ref=unit.name,
        owner=str(owner),
        program=program,
        input=channel,
        supplies=list(credentials.supplied),
        privilege=privilege,
        available=available,
        problems=problems,
    )


def _systemd_privilege(unit: SystemdUnit, takes_connections: bool) -> str:
    """same_user | needs_admin | unknown - asked, never tried."""
    if not unit.installed:
        return "unknown"
    if takes_connections:
        # systemd starts the service itself when someone connects; the only
        # question is whether this user may connect.
        answers = {probes.socket_permits(listen) for listen in unit.socket.listen} if unit.socket and unit.socket.listen else {None}
        if answers == {True}:
            return "same_user"
        return "needs_admin" if False in answers else "unknown"
    if unit.scope == "user":
        return "same_user"  # this user's own systemd; nobody else to ask
    allowed = probes.may_manage_system_units()
    return {True: "same_user", False: "needs_admin"}.get(allowed, "unknown")


# --------------------------------------------------------------------------- Compose

def compose_context(container: dict[str, Any], owner: Path, credentials: Credentials) -> ExecutionContext:
    """A Compose service a one-off container could be run from.

    `docker compose run <service> <values>` keeps the service's entrypoint
    and replaces its command with the values. So the entrypoint is the
    program, and it has to be one that treats what follows as data: with no
    entrypoint at all, whoever runs it chooses the program.
    """
    entrypoint = container.get("entrypoint")
    problems: list[str] = []
    if entrypoint:
        program = list(entrypoint)
        if not program_is_fixed(program):
            problems.append("program_not_fixed")
    else:
        program = []
        # An image Bevro cannot look inside may have an entrypoint of its own;
        # one built from this project has none, which is worse, not unknown.
        problems.append("program_not_fixed" if container.get("built_here") else "program_unknown")
    permitted = probes.docker_permitted()
    privilege = {True: "same_user", False: "needs_admin"}.get(permitted, "unknown")
    if privilege == "needs_admin":
        problems.append("needs_admin")
    elif privilege == "unknown":
        problems.append("permission_unknown")
    return ExecutionContext(
        kind=ContextKind.COMPOSE_RUN,
        source_ref=f"{container['file']}:{container['service']}",
        owner=str(owner),
        program=program,
        input=ContextInput.ARGUMENT_DATA,
        supplies=list(credentials.supplied),
        privilege=privilege,
        available=True,
        problems=problems,
    )


# --------------------------------------------------------------------------- linking

def link_contexts(runtimes: list[RuntimeProfile]) -> list[RuntimeProfile]:
    """Say which way in each context would carry, if any.

    A context is only interesting relative to a program Bevro would run:
    the same program, started by something that hands it more. A context
    whose fixed program is none of them is recorded as such, and is no
    help to any of them.
    """
    interfaces = [rt for rt in runtimes if rt.context is None and rt.kind in WORK_INTERFACES and (rt.adapter.get("config") or {}).get("argv")]
    for rt in runtimes:
        ctx = rt.context
        if ctx is None:
            continue
        problems = [p for p in ctx.problems if p != "no_matching_program"]
        match = None
        if ctx.program and program_is_fixed(ctx.program):
            host = ctx.kind != ContextKind.COMPOSE_RUN
            match = next((i for i in interfaces if same_program(ctx.program, list(i.adapter["config"]["argv"]), host=host)), None)
            if match is None:
                problems.append("no_matching_program")
        rt.context = ctx.model_copy(update={"matches": match.id if match else None, "problems": problems})
    return runtimes
