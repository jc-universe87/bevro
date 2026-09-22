"""ConnectionDiscoveryService: pick the strategy for a target and produce a draft.

Deterministic first; the optional model assist may then refine the draft's
wording and capabilities from a sanitised summary. Strategies are registered
here so adding a kind of target is one class and one line.
"""

from __future__ import annotations

import logging

from app.connect import assist
from app.connect.draft import ProviderDraft
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed, DiscoveryStrategy
from app.connect.strategies.command import CommandStrategy
from app.connect.strategies.http import HttpDiscoveryStrategy
from app.connect.strategies.local import LocalProjectStrategy
from app.connect.targets import ConnectTarget

log = logging.getLogger("bevro.connect")


class ConnectionDiscoveryService:
    def __init__(self, strategies: list[DiscoveryStrategy] | None = None, *, use_assist: bool = True) -> None:
        self.strategies: list[DiscoveryStrategy] = strategies if strategies is not None else [HttpDiscoveryStrategy(), LocalProjectStrategy(), CommandStrategy()]
        self.use_assist = use_assist

    def strategy_for(self, target: ConnectTarget) -> DiscoveryStrategy:
        for strategy in self.strategies:
            if strategy.supports(target):
                return strategy
        raise DiscoveryFailed("Bevro doesn't know how to connect that kind of thing yet.")

    def discover(self, target: ConnectTarget, context: DiscoveryContext | None = None) -> ProviderDraft:
        context = context or DiscoveryContext()
        strategy = self.strategy_for(target)
        log.info("discovery: %s via %s", target.kind, strategy.name)
        draft = strategy.discover(target, context)
        if self.use_assist and draft.invocable:
            draft = assist.refine(draft, draft.assist_evidence)
        return draft


_service: ConnectionDiscoveryService | None = None


def get_discovery_service() -> ConnectionDiscoveryService:
    global _service
    if _service is None:
        _service = ConnectionDiscoveryService()
    return _service


def set_discovery_service(service: ConnectionDiscoveryService | None) -> None:
    global _service
    _service = service
