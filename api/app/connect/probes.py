"""Read-only looks at the host around a project: what is already running, what
is managed. Nothing here starts, stops or changes anything.

- listening_processes(root): this user's processes whose working directory is
  inside the project, with the TCP ports they listen on (from /proc).
- port_answers(port): does anything answer HTTP on localhost:port?
- systemd_units(project): *.service files shipped with the project, parsed for
  ExecStart / WorkingDirectory / EnvironmentFile, and whether systemd says the
  unit is active (a read-only `systemctl show`; absent systemctl means unknown).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.connect.inspect import Project

MAX_PROCESSES = 4000
_UNIT_KEY = re.compile(r"^\s*(ExecStart|WorkingDirectory|EnvironmentFile|Type|Description)\s*=\s*(.*)$")
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
    environment_file: str | None = None
    unit_type: str = "simple"
    active: bool | None = None  # None = unknown (no systemctl, or not installed)
    installed: bool = False


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
                unit.environment_file = value.lstrip("-")
            elif key == "Type":
                unit.unit_type = value
            elif key == "Description":
                unit.description = value
        unit.active, unit.installed = _unit_state(unit.name)
        units.append(unit)
    return units
