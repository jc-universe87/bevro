"""Read-only looks at the host around a project: what is already running, what
is managed. Nothing here starts, stops or changes anything.

- listening_processes(root): this user's processes whose working directory is
  inside the project, with the TCP ports they listen on (from /proc).
- port_answers(port): does anything answer HTTP on localhost:port?
- systemd_units(project): *.service files shipped with the project, and the
  installed unit's own fragment and drop-ins, parsed for ExecStart /
  WorkingDirectory / Environment / EnvironmentFile, and whether systemd says the
  unit is active (a read-only `systemctl show`; absent systemctl means unknown).
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
_UNIT_KEY = re.compile(r"^\s*(ExecStart|WorkingDirectory|EnvironmentFile|Environment|Type|Description)\s*=\s*(.*)$")
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

    @property
    def carries_credentials(self) -> bool:
        """Does this unit hand its process an environment of its own?"""
        return bool(self.environment_names) or any(ref.likely_present for ref in self.environment_files)


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


def _unit_state(name: str) -> tuple[bool | None, bool]:
    if shutil.which("systemctl") is None:
        return None, False
    for scope in ((), ("--user",)):
        try:
            out = subprocess.run(["systemctl", *scope, "show", "-p", "LoadState,ActiveState", "--value", name], capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        if len(lines) >= 2 and lines[0] == "loaded":
            return lines[1] == "active", True
    return None, False


def systemd_units(project: Project) -> list[SystemdUnit]:
    units: list[SystemdUnit] = []
    candidates: list[str] = []
    for rel in (".", "deploy", "deploy/systemd", "systemd", "etc", "etc/systemd", "service", "services", "ops"):
        for name in project.listdir(rel):
            if name.endswith(".service"):
                candidates.append(f"{rel}/{name}" if rel != "." else name)
    for rel in candidates[:10]:
        text = project.read_text(rel, 16_000)
        if text is None:
            continue
        unit = SystemdUnit(name=Path(rel).name)
        for line in text.splitlines():
            m = _UNIT_KEY.match(line)
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            if key == "ExecStart":
                unit.exec_start = value
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
        unit.active, unit.installed = _unit_state(unit.name)
        _add_installed_detail(unit)
        seen: set[str] = set()
        unit.environment_files = [ref for ref in unit.environment_files if not (ref.path in seen or seen.add(ref.path))]
        unit.environment_names = sorted(dict.fromkeys(unit.environment_names))
        unit.environment_file = unit.environment_files[0].path if unit.environment_files else None
        units.append(unit)
    return units


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
    configuration and world-readable. `systemctl show -p Environment` is
    deliberately not used: it prints merged *values*, and Bevro has no reason
    to have them in memory.
    """
    if not unit.installed or shutil.which("systemctl") is None:
        return
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "FragmentPath,DropInPaths", "--value", unit.name],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    lines = out.stdout.splitlines()
    unit.fragment_path = (lines[0].strip() or None) if lines else None
    unit.drop_ins = (lines[1].split() if len(lines) > 1 else [])[:10]
    for path in [p for p in (unit.fragment_path, *unit.drop_ins) if p]:
        _read_unit_file(Path(path), unit)


def _read_unit_file(path: Path, unit: SystemdUnit) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:16_000]
    except OSError:
        return
    for line in text.splitlines():
        match = _UNIT_KEY.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "EnvironmentFile":
            unit.environment_files.append(_environment_file(value))
        elif key == "Environment":
            unit.environment_names += _environment_names(value)
