"""Bits every HTTP-backed routing model needs."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.routing.catalogue import CatalogueEntry, catalogue_json
from app.routing.decision import RoutingDecision, RoutingSource
from app.routing.models import RoutingModelError

MAX_REQUEST_CHARS = 2000


def user_message(request: str, catalogue: list[CatalogueEntry], context: dict[str, Any]) -> str:
    """Exactly what the model sees besides the system prompt: the request,
    the sanitised catalogue, and at most a previous question/answer pair."""
    payload: dict[str, Any] = {
        "request": " ".join(request.split())[:MAX_REQUEST_CHARS],
        "providers": catalogue_json(catalogue),
    }
    if context.get("previous_question") and context.get("answer"):
        payload["previous_question"] = str(context["previous_question"])[:200]
        payload["answer"] = str(context["answer"])[:500]
    return json.dumps(payload, ensure_ascii=False)


def parse_decision(raw: Any) -> RoutingDecision:
    """Strict: the model's output must validate as-is. No guessing."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise RoutingModelError(f"routing model returned non-JSON output: {exc}") from exc
    if not isinstance(raw, dict):
        raise RoutingModelError("routing model returned an unexpected shape")
    raw.pop("routing_source", None)
    try:
        decision = RoutingDecision.model_validate(raw)
    except ValidationError as exc:
        raise RoutingModelError(f"routing model output failed validation: {exc.error_count()} error(s)") from exc
    return decision.model_copy(update={"routing_source": RoutingSource.LLM})
