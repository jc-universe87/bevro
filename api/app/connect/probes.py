"""Read-only looks at the host around a project: what is already running, what
is managed. Nothing here starts, stops or changes anything.

- listening_processes(root): this user's processes whose working directory is
  inside the project, with the TCP ports they listen on (from /proc).
- port_answers(port): does anything answer HTTP on localhost:port?
- systemd_units(project): *.service files shipped with the project, and the
  installed unit's own fragment and drop-ins, parsed for ExecStart /
  WorkingDirectory / Environment / EnvironmentFile, and whether systemd says the
  unit is active (a read-only `systemctl show`; absent systemctl means unknown).
  A template unit is found through `list-unit-files`, and a `.socket` that
  starts the service is read for its shape.
- may_manage_system_units(), socket_permits(), docker_permitted(): would this
  user be let in? Asked of PolicyKit and of file permissions, never by trying.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.connect.inspect import Project

MAX_PROCESSES = 4000
_UNIT_KEY = re.compile(r"^\s*(ExecStart|WorkingDirectory|EnvironmentFile|Environment|Type|Description|StandardInput)\s*=\s*(.*)$")
_SOCKET_KEY = re.compile(r"^\s*(Accept|ListenStream)\s*=\s*(.*)$")
# An environment variable name, in the form a unit file writes one.
_ENV_NAME = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_LISTEN = "0A"  # TCP_LISTEN in /proc/net/tcp


@dataclass
class RunningProcess:
    pid: int
    program: str  # basename only
    ports: list[int] = field(default_factory=list)


def _listening_inodes() -> dict[str, int]:
    """socket inode -> port for every listening TCP socket on this host."""
    out: dict[str, int] = {}
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name, encoding="ascii") as fh:
                next(fh)
                for line in fh:
                    parts = line.split()
                    if len(parts) > 9 and parts[3] == _LISTEN:
                        port = int(parts[1].rsplit(":", 1)[1], 16)
                        out[parts[9]] = port
        except (OSError, ValueError, StopIteration):
            continue
    return out


def listening_processes(root: Path) -> list[RunningProcess]:
    inodes = _listening_inodes()
    found: list[RunningProcess] = []
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()][:MAX_PROCESSES]
    except OSError:
        return found
    for entry in entries:
        proc = Path("/proc") / entry
        try:
            cwd = os.readlink(proc / "cwd")
        except OSError:
            continue  # another user's process, or gone
        cwd_path = Path(cwd)
        if cwd_path != root and root not in cwd_path.parents:
            continue
        try:
            program = (proc / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            program = "?"
        ports: list[int] = []
        try:
            for fd in os.listdir(proc / "fd"):
                try:
                    link = os.readlink(proc / "fd" / fd)
                except OSError:
                    continue
                if link.startswith("socket:["):
                    inode = link[8:-1]
                    if inode in inodes and inodes[inode] not in ports:
                        ports.append(inodes[inode])
        except OSError:
            pass
        found.append(RunningProcess(pid=int(entry), program=program, ports=sorted(ports)))
    return found


def port_answers(port: int, timeout: float = 1.5) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/", timeout=timeout, follow_redirects=False)
    except httpx.HTTPError:
        return False
    return r.status_code < 500 or r.status_code in (500, 501, 503)


@dataclass
class SocketUnit:
    """A .socket unit that starts a service when something connects to it.

    Read for its shape only: whether each connection gets a service of its
    own (`Accept=yes`), and where it listens. Who may connect is decided by
    the operating system, from the socket's own permissions.
    """

    name: str
    accept: bool = False
    listen: list[str] = field(default_factory=list)
    installed: bool = False


@dataclass
class SystemdUnit:
    name: str
    description: str | None = None
    exec_start: str | None = None
    working_directory: str | None = None
    environment_file: str | None = None      # the first one, kept for older callers
    unit_type: str = "simple"
    active: bool | None = None  # None = unknown (no systemctl, or not installed)
    installed: bool = False
    # Every EnvironmentFile= the unit and its drop-ins declare, with what
    # could be told about each without opening it.
    environment_files: list["EnvironmentFileRef"] = field(default_factory=list)
    # Names from Environment=NAME=value lines. Names only: a unit file is
    # configuration and may be read, but a value in one is still a value.
    environment_names: list[str] = field(default_factory=list)
    # Where the unit is really defined, once systemd has had its say.
    fragment_path: str | None = None
    drop_ins: list[str] = field(default_factory=list)
    # "system" or "user": which systemd manages it, once installed.
    scope: str | None = None
    standard_input: str | None = None
    # The socket that starts it, when one does.
    socket: SocketUnit | None = None

    @property
    def carries_credentials(self) -> bool:
        """Does this unit hand its process an environment of its own?"""
        return bool(self.environment_names) or any(ref.likely_present for ref in self.environment_files)

    @property
    def template(self) -> bool:
        """`name@.service`: one unit file, started once per short name."""
        return self.name.endswith("@.service")


@dataclass
class EnvironmentFileRef:
    """A file a unit points at, described without being opened.

    `state` is the whole point of this class:

        present    it is there
        protected  something is there and Bevro may not look - which is what
                   a credentials file owned by root looks like, and is
                   evidence *for* it rather than against
        missing    the directory can be read and the file is not in it
        optional   the unit marked it with "-", so its absence means nothing

    Bevro never opens one. Not the values, not the names, not one byte.
    """

    path: str
    state: str = "unknown"
    optional: bool = False

    @property
    def likely_present(self) -> bool:
        return self.state in ("present", "protected", "unknown")


SCOPES = (("system", ()), ("user", ("--user",)))


def _systemctl(*args: str) -> str | None:
    """A read-only systemctl query, or None where systemctl cannot answer."""
    if shutil.which("systemctl") is None:
        return None
    try:
        out = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _unit_state(name: str) -> tuple[bool | None, bool, str | None]:
    """(active, installed, scope). A template is never active itself."""
    for scope, flag in SCOPES:
        if name.endswith("@.service"):
            # systemd will not `show` a template, but does list its file.
            listed = _systemctl(*flag, "list-unit-files", "--no-legend", name) or ""
            if any(line.split()[:1] == [name] for line in listed.splitlines()):
                return None, True, scope
            continue
        shown = _systemctl(*flag, "show", "-p", "LoadState,ActiveState", "--value", name) or ""
        lines = [ln.strip() for ln in shown.splitlines() if ln.strip()]
        if len(lines) >= 2 and lines[0] == "loaded":
            return lines[1] == "active", True, scope
    return None, False, None


def _unit_files(unit_name: str, scope: str | None) -> list[str]:
    """The fragment and drop-ins systemd uses for a unit, by path.

    `systemctl cat` names each file it shows in a header line; only those
    lines are taken, and the files are then read like any other unit file.
    """
    flag = ("--user",) if scope == "user" else ()
    if unit_name.endswith("@.service"):
        text = _systemctl(*flag, "cat", "--no-pager", unit_name) or ""
        return [line[2:].strip() for line in text.splitlines() if line.startswith("# /")][:11]
    shown = _systemctl(*flag, "show", "-p", "FragmentPath,DropInPaths", "--value", unit_name) or ""
    lines = shown.splitlines()
    fragment = (lines[0].strip() or None) if lines else None
    drop_ins = (lines[1].split() if len(lines) > 1 else [])[:10]
    return [p for p in (fragment, *drop_ins) if p]


def systemd_units(project: Project) -> list[SystemdUnit]:
    units: list[SystemdUnit] = []
    candidates: list[str] = []
    sockets: dict[str, str] = {}
    for rel in (".", "deploy", "deploy/systemd", "systemd", "etc", "etc/systemd", "service", "services", "ops"):
        for name in project.listdir(rel):
            path = f"{rel}/{name}" if rel != "." else name
            if name.endswith(".service"):
                candidates.append(path)
            elif name.endswith(".socket"):
                sockets.setdefault(name, path)
    for rel in candidates[:10]:
        text = project.read_text(rel, 16_000)
        if text is None:
            continue
        unit = SystemdUnit(name=Path(rel).name)
        _apply_unit_text(text, unit)
        unit.active, unit.installed, unit.scope = _unit_state(unit.name)
        _add_installed_detail(unit)
        unit.socket = _socket_for(unit, project, sockets)
        seen: set[str] = set()
        unit.environment_files = [ref for ref in unit.environment_files if not (ref.path in seen or seen.add(ref.path))]
        unit.environment_names = sorted(dict.fromkeys(unit.environment_names))
        unit.environment_file = unit.environment_files[0].path if unit.environment_files else None
        units.append(unit)
    return units


def _apply_unit_text(text: str, unit: SystemdUnit) -> None:
    """Fold one unit file into what is known, the way systemd would.

    Later files win for single values - the installed fragment over what the
    project ships, a drop-in over the fragment - and an empty `ExecStart=`
    clears what came before, as it does in a drop-in. Environment lines
    accumulate.
    """
    for line in text.splitlines():
        match = _UNIT_KEY.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "ExecStart":
            unit.exec_start = value or None
        elif key == "WorkingDirectory":
            unit.working_directory = value
        elif key == "EnvironmentFile":
            unit.environment_files.append(_environment_file(value))
        elif key == "Environment":
            unit.environment_names += _environment_names(value)
        elif key == "Type":
            unit.unit_type = value
        elif key == "Description":
            unit.description = value
        elif key == "StandardInput":
            unit.standard_input = value


def _environment_names(value: str) -> list[str]:
    """The names a `Environment=` line sets. Never what it sets them to."""
    names: list[str] = []
    for part in shlex.split(value, posix=True) if value else []:
        match = _ENV_NAME.match(part)
        if match:
            names.append(match.group(1))
    return names


def _environment_file(value: str) -> EnvironmentFileRef:
    """What can be said about a file a unit points at, without opening it."""
    optional = value.startswith("-")
    path = Path(os.path.expanduser(value.lstrip("-").strip().strip('"')))
    return EnvironmentFileRef(path=str(path), state=_file_state(path), optional=optional)


def _file_state(path: Path) -> str:
    try:
        path.stat()
    except PermissionError:
        # Something is there and Bevro may not look at it. For a credentials
        # file that is the correct state of affairs, not a problem.
        return "protected"
    except FileNotFoundError:
        # Not being allowed to look in the directory is different from the
        # directory not being there. Only the first leaves the question open.
        try:
            path.parent.stat()
        except PermissionError:
            return "unknown"
        except OSError:
            return "missing"
        return "missing"
    except OSError:
        return "unknown"
    return "present"


def _add_installed_detail(unit: SystemdUnit) -> None:
    """What systemd itself says about this unit, including its drop-ins.

    Only unit files are read - the fragment and its drop-ins, which are
    configuration and world-readable. Once a unit is installed, those are
    what systemd runs, so they are taken over what the project ships.
    `systemctl show -p Environment` is deliberately not used: it prints
    merged *values*, and Bevro has no reason to have them in memory.
    """
    if not unit.installed:
        return
    paths = _unit_files(unit.name, unit.scope)
    if paths:
        unit.fragment_path, unit.drop_ins = paths[0], paths[1:]
    for path in paths:
        _read_unit_file(Path(path), unit)


def _read_unit_file(path: Path, unit: SystemdUnit) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:16_000]
    except OSError:
        return
    _apply_unit_text(text, unit)


def _socket_for(unit: SystemdUnit, project: Project, shipped: dict[str, str]) -> SocketUnit | None:
    """The socket that starts this service, if there is one.

    systemd pairs `name.socket` with `name.service`, or with `name@.service`
    when every connection gets a service of its own.
    """
    stem = unit.name.removesuffix(".service").removesuffix("@")
    name = f"{stem}.socket"
    socket = SocketUnit(name=name)
    found = False
    if name in shipped:
        text = project.read_text(shipped[name], 16_000)
        if text is not None:
            _apply_socket_text(text, socket)
            found = True
    _active, socket.installed, scope = _unit_state(name)
    if socket.installed:
        for path in _unit_files(name, scope):
            try:
                _apply_socket_text(Path(path).read_text(encoding="utf-8", errors="replace")[:16_000], socket)
                found = True
            except OSError:
                continue
    return socket if found else None


def _apply_socket_text(text: str, socket: SocketUnit) -> None:
    for line in text.splitlines():
        match = _SOCKET_KEY.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "Accept":
            socket.accept = value.lower() in ("yes", "true", "1", "on")
        elif key == "ListenStream" and value and value not in socket.listen:
            socket.listen.append(value)


# --------------------------------------------------------------------------- who may use what

def may_manage_system_units() -> bool | None:
    """Would the system let this process start a system unit, without asking anyone?

    PolicyKit decides who may start and stop system services. `pkcheck`
    asks it the question without starting anything and without offering
    to prompt for a password - so "it would need a password" comes back
    as no, which is the true answer for a worker nobody is sitting at.
    None when there is no way to ask.
    """
    if os.geteuid() == 0:
        return True
    if shutil.which("pkcheck") is None:
        return None
    try:
        out = subprocess.run(
            ["pkcheck", "--action-id", "org.freedesktop.systemd1.manage-units", "--process", str(os.getpid())],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # 0 allowed; 1 not allowed; 2 allowed only after authenticating; 3 that was dismissed.
    return {0: True, 1: False, 2: False, 3: False}.get(out.returncode)


def socket_permits(listen: str) -> bool | None:
    """May this user connect to a socket a unit listens on?

    A socket in the filesystem is guarded by its own permissions, which the
    operating system enforces on every connection - Bevro only asks whether
    it would be let in. A network port or an abstract socket is open to any
    process on the machine.
    """
    if not listen.startswith("/"):
        return True
    path = Path(listen)
    try:
        path.stat()
    except PermissionError:
        return False
    except OSError:
        return None  # not listening now; nobody can say
    return os.access(path, os.R_OK | os.W_OK)


def docker_permitted() -> bool | None:
    """May this user drive Docker here?

    Asked of the socket's permissions, never of Docker itself: nothing is
    run. Being allowed is not a small thing - whoever can use the Docker
    socket can do anything root can - which is why using it for work is
    Bevro's own decision to make carefully, not merely the system's.
    """
    if shutil.which("docker") is None:
        return False
    host = os.environ.get("DOCKER_HOST", "")
    if host and not host.startswith("unix://"):
        return None  # a daemon somewhere else; its permissions are not visible from here
    path = Path(host.removeprefix("unix://") if host else "/var/run/docker.sock")
    try:
        path.stat()
    except PermissionError:
        return False
    except OSError:
        return False
    return os.access(path, os.R_OK | os.W_OK)
