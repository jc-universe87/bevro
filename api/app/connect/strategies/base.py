from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.connect.draft import ProviderDraft
from app.connect.targets import ConnectTarget


@dataclass
class DiscoveryContext:
    """What a strategy is allowed to work with.

    `roots` are the approved local directories of the process doing the
    discovery (the worker, or an API on the host). `secrets` are credentials
    the person typed on the Connect screen for probing (never stored here).
    """

    roots: list[Path] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)
    timeout: float = 6.0
    # Tests hand in an httpx transport so nothing leaves the process.
    transport: Any = None


class DiscoveryStrategy(Protocol):
    name: str

    def supports(self, target: ConnectTarget) -> bool: ...

    def discover(self, target: ConnectTarget, context: DiscoveryContext) -> ProviderDraft: ...


class DiscoveryFailed(Exception):
    """Discovery could not produce a draft. The message is for the person."""
