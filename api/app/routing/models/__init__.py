"""Routing-model backends: the thing that turns request + catalogue into a
structured decision. One interface, several vendors, none privileged."""

from __future__ import annotations

from typing import Any, Protocol

from app.config import Settings, get_settings
from app.routing.catalogue import CatalogueEntry
from app.routing.decision import RoutingDecision


class RoutingModelError(Exception):
    """Anything that stops a backend from producing a valid decision:
    network, auth, timeout, malformed output. The caller falls back."""


class RoutingModel(Protocol):
    name: str

    def route(self, request: str, catalogue: list[CatalogueEntry], context: dict[str, Any]) -> RoutingDecision: ...

    def structured(self, system: str, user: str, schema: dict[str, Any], name: str, max_tokens: int = 400) -> Any:
        """Optional: one structured completion for other small classification
        jobs (Connect's discovery assist). Same key, same sanitisation rules."""
        ...


def get_routing_model(settings: Settings | None = None) -> RoutingModel:
    settings = settings or get_settings()
    backend = settings.router_backend.lower()
    if backend == "openai":
        from app.routing.models.openai import OpenAIRoutingModel

        return OpenAIRoutingModel(settings)
    if backend == "anthropic":
        from app.routing.models.anthropic import AnthropicRoutingModel

        return AnthropicRoutingModel(settings)
    raise RoutingModelError(f"unknown routing backend {settings.router_backend!r}")
