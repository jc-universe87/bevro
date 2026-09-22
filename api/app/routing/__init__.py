"""Routing: deciding which provider should take a request.

Routing is separate from execution on purpose. A router — deterministic
rules or a language model — returns a `RoutingDecision`. Bevro validates it
against the provider registry and only then creates the Task and ProviderRun
that the existing services execute. Nothing in this package runs providers,
touches the filesystem or changes task state.
"""

from app.routing.decision import InputRequestSpec, PlanStep, RoutingDecision, RoutingSource
from app.routing.router import Router, get_router, set_router

__all__ = ["InputRequestSpec", "PlanStep", "RoutingDecision", "RoutingSource", "Router", "get_router", "set_router"]
