"""The Router interface the task service depends on, and how one is chosen."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.config import get_settings
from app.routing.decision import RoutingDecision
from app.routing.deterministic import DeterministicRouter

log = logging.getLogger("bevro.routing")


class Router(Protocol):
    name: str

    def route(self, db: Session, request: str, context: dict[str, Any] | None = None) -> RoutingDecision: ...


_router: Router | None = None


def build_router() -> Router:
    settings = get_settings()
    mode = settings.router_mode.lower()
    if mode == "llm":
        from app.routing.llm import LLMRouter
        from app.routing.models import RoutingModelError, get_routing_model

        try:
            model = get_routing_model(settings)
        except RoutingModelError as exc:
            log.warning("router mode is llm but the backend could not be set up (%s); using deterministic routing", exc)
            return DeterministicRouter()
        log.info("intelligent routing enabled: backend=%s model=%s", model.name, getattr(model, "model", "?"))
        return LLMRouter(model)
    if mode != "deterministic":
        log.warning("unknown BEVRO_ROUTER_MODE %r; using deterministic routing", settings.router_mode)
    return DeterministicRouter()


def get_router() -> Router:
    global _router
    if _router is None:
        _router = build_router()
    return _router


def set_router(router: Router | None) -> None:
    """Swap the router (tests) or reset to configuration (None)."""
    global _router
    _router = router


def routing_status() -> dict[str, Any]:
    """For the Settings screen: mode only, never keys or endpoints."""
    router = get_router()
    return {"mode": "llm" if router.name == "llm" else "deterministic"}
