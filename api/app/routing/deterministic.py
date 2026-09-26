"""Rule-based routing: the offline default, the fallback, and the test double.

The same fit scorer Home uses (app.routing.fit), limited to what a Router
may choose: providers Bevro can send work to now. It picks one only when one
clearly fits; several that fit about as well, or none, is no selection.
Nothing here names a provider.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.routing import fit as fitting
from app.routing.catalogue import CatalogueEntry, build_catalogue
from app.routing.decision import InputRequestSpec, RoutingDecision, RoutingSource

UNAVAILABLE_PREFIX = "unavailable:"  # reason = "unavailable:<slug>" when what fits exists but cannot run now


def _item(entry: CatalogueEntry) -> SimpleNamespace:
    """A catalogue entry, shaped like the provider it came from, for the fit scorer."""
    return SimpleNamespace(
        id=entry.id,
        name=entry.name,
        description=entry.description,
        source_description=None,
        capabilities=[c.model_dump(exclude_none=True) for c in entry.capabilities],
    )


class DeterministicRouter:
    name = "deterministic"

    def decide(self, request: str, catalogue: list[CatalogueEntry]) -> RoutingDecision:
        """`catalogue` may include unavailable entries; they are never selected."""
        usable = {e.id: e for e in catalogue if e.available and e.can_invoke}
        fit = fitting.decide(request, [_item(e) for e in usable.values()])
        if fit.kind == "one" and fit.best is not None:
            words = ", ".join(w for w, _f, _p in fit.best.evidence[:4])
            return self._select(usable[fit.best.item.id], f"{fit.reason}: {words}", 0.8 if fit.sure else 0.5)
        if fit.kind == "choice":
            return RoutingDecision(routing_source=RoutingSource.DETERMINISTIC, reason="several providers fit about as well")
        # Something that can't run now may be what fits: say so rather than nothing.
        idle = [e for e in catalogue if e.id not in usable]
        if idle:
            other = fitting.decide(request, [_item(e) for e in idle])
            if other.kind == "one" and other.best is not None:
                return RoutingDecision(routing_source=RoutingSource.DETERMINISTIC, reason=f"{UNAVAILABLE_PREFIX}{other.best.item.id}")
        return RoutingDecision(routing_source=RoutingSource.DETERMINISTIC, reason="nothing fits the request")

    @staticmethod
    def _select(entry: CatalogueEntry, rationale: str, confidence: float) -> RoutingDecision:
        needs_workspace = "workspace" in entry.requires
        return RoutingDecision(
            selected_provider_ids=[entry.id],
            needs_input=needs_workspace,
            input_request=InputRequestSpec(kind="workspace", prompt="Which project should I work on?") if needs_workspace else None,
            rationale=rationale,
            confidence=confidence,
            routing_source=RoutingSource.DETERMINISTIC,
        )

    def route(self, db: Session, request: str, context: dict | None = None) -> RoutingDecision:
        return self.decide(request, build_catalogue(db, selectable_only=False))


def unavailable_message(decision: RoutingDecision) -> str | None:
    """The sentence to show when the decision says 'exists but unavailable'."""
    if decision.reason and decision.reason.startswith(UNAVAILABLE_PREFIX):
        return "The app or agent for that isn't available on this installation right now."
    return None
