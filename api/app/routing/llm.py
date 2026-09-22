"""LLM routing with validation and deterministic fallback.

    request → routing model → RoutingDecision → validate → (ok) decision
                                    ↓ any failure
                            DeterministicRouter (routing_source = fallback)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.routing.catalogue import build_catalogue
from app.routing.decision import RoutingDecision, RoutingSource
from app.routing.deterministic import DeterministicRouter
from app.routing.models import RoutingModel, RoutingModelError
from app.routing.validate import InvalidDecision, validate_decision

log = logging.getLogger("bevro.routing")


class LLMRouter:
    name = "llm"

    def __init__(self, model: RoutingModel, fallback: DeterministicRouter | None = None) -> None:
        self.model = model
        self.fallback = fallback or DeterministicRouter()
        # Set on each call so the task service can record why a fallback happened.
        self.last_fallback_reason: str | None = None

    def route(self, db: Session, request: str, context: dict[str, Any] | None = None) -> RoutingDecision:
        context = context or {}
        full = build_catalogue(db, selectable_only=False)
        catalogue = [e for e in full if e.available and e.can_invoke]  # the model only ever sees usable providers
        self.last_fallback_reason = None
        log.info("routing started backend=%s providers=%s", self.model.name, [e.id for e in catalogue])
        if not catalogue:
            log.info("routing: empty catalogue, nothing to select")
            return self.fallback.decide(request, full).model_copy(update={"routing_source": RoutingSource.FALLBACK})
        started = time.monotonic()
        try:
            decision = self.model.route(request, catalogue, context)
            validate_decision(db, decision)
        except (RoutingModelError, InvalidDecision) as exc:
            self.last_fallback_reason = f"{type(exc).__name__}: {exc}"
            log.warning("routing fell back to deterministic rules after %.2fs: %s", time.monotonic() - started, self.last_fallback_reason)
            fallback = self.fallback.decide(request, full)
            return fallback.model_copy(update={"routing_source": RoutingSource.FALLBACK})
        except Exception as exc:  # noqa: BLE001 - a router bug must never block a task
            self.last_fallback_reason = f"unexpected {type(exc).__name__}"
            log.exception("routing model raised; falling back")
            fallback = self.fallback.decide(request, full)
            return fallback.model_copy(update={"routing_source": RoutingSource.FALLBACK})
        log.info(
            "routing succeeded in %.2fs selected=%s needs_input=%s confidence=%s",
            time.monotonic() - started, decision.selected_provider_ids, decision.needs_input, decision.confidence,
        )
        return decision
