"""A decision is a proposal. This module says whether Bevro will act on it."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from adapters import get_adapter
from adapters.base import NotSupported
from app.models import Provider
from app.routing.decision import RoutingDecision
from app.services.providers import get_by_slug, is_available

MAX_SELECTED = 1  # the execution engine runs one provider per task today
SUPPORTED_INPUT_KINDS = {"workspace", "question"}


class InvalidDecision(Exception):
    pass


@dataclass
class ValidatedDecision:
    decision: RoutingDecision
    provider: Provider | None  # None means "nothing suitable", which is a valid outcome


def validate_decision(db: Session, decision: RoutingDecision) -> ValidatedDecision:
    ids = decision.selected_provider_ids
    if len(ids) > MAX_SELECTED:
        raise InvalidDecision(f"selected {len(ids)} providers; multi-provider execution is not supported yet")
    if not ids:
        # "Nothing suitable" is a valid outcome. A stray needs_input alongside it means nothing.
        return ValidatedDecision(decision=decision.model_copy(update={"needs_input": False, "input_request": None}), provider=None)
    if decision.needs_input:
        if decision.input_request is None:
            raise InvalidDecision("needs_input without an input_request")
        if decision.input_request.kind not in SUPPORTED_INPUT_KINDS:
            raise InvalidDecision(f"unsupported input kind {decision.input_request.kind!r}")

    provider = get_by_slug(db, ids[0])
    if provider is None:
        raise InvalidDecision(f"unknown provider {ids[0]!r}")
    if not provider.enabled:
        raise InvalidDecision(f"provider {ids[0]!r} is disabled")
    if not is_available(provider):
        raise InvalidDecision(f"provider {ids[0]!r} is not available")
    from app.services.runtime import requires_of

    try:
        get_adapter(str(provider.adapter.get("kind", "")))
    except NotSupported:
        raise InvalidDecision(f"provider {ids[0]!r} cannot be invoked") from None
    if decision.input_request and decision.input_request.kind == "workspace" and "workspace" not in requires_of(provider):
        raise InvalidDecision(f"provider {ids[0]!r} does not take a workspace")
    for step in decision.plan:
        if step.provider_id and step.provider_id not in ids:
            raise InvalidDecision(f"plan references unselected provider {step.provider_id!r}")
    return ValidatedDecision(decision=decision, provider=provider)
