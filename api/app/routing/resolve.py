"""From a request to an answer: which app or agent, and how to use it now.

    request
      -> fit (app.routing.fit): which of the person's items fits, and how surely
      -> way in: can Bevro send it the work, or where does the person go instead
      -> an Answer, in plain words

Two questions, asked in that order. The right tool is chosen on what it is
for; only then is it asked whether Bevro can reach it. An app that fits but
cannot be driven is still the answer - opened, or explained - and never
swapped for a worse one that happens to be reachable.

Nothing here runs anything or creates a task. The task service acts on an
Answer whose outcome is "direct"; every other outcome is something to show.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import Provider
from app.routing import fit as fitting
from app.routing.decision import RoutingDecision

log = logging.getLogger("bevro.routing")

# What Bevro does with the chosen item.
DIRECT = "direct"  # Bevro sends it the work
HANDOFF = "handoff"  # the person opens its own app
BLOCKED = "blocked"  # Bevro could, once it has a credential
CHOOSE = "choose"  # Bevro could, once the person says which one it is for
UNAVAILABLE = "unavailable"  # Bevro could, but can't reach it right now
HOW_TO = "how_to"  # no app to open, but the person has a way to use it
SETUP = "setup"  # known, and nothing yet says how it is used
CHOICE = "choice"
NONE = "none"


@dataclass
class Option:
    provider: Provider
    summary: str


@dataclass
class Answer:
    outcome: str
    message: str
    sure: bool = False
    provider: Provider | None = None
    why: str | None = None
    choices: list[Option] = field(default_factory=list)
    # Internal, for the task's routing record and the log. Never shown.
    rationale: str = ""
    source: str = "deterministic"
    # The routing model's own decision, when it made this one.
    decision: RoutingDecision | None = None

    def public(self) -> dict[str, Any]:
        """What the browser gets: names and plain sentences, nothing more."""
        return {
            "outcome": self.outcome,
            "message": self.message,
            "sure": self.sure,
            "item": {"id": str(self.provider.id), "name": self.provider.name} if self.provider else None,
            "why": self.why,
            "choices": [{"id": str(o.provider.id), "name": o.provider.name, "summary": o.summary} for o in self.choices],
        }


NO_MATCH = "I don't have an app or agent that looks suited to this yet."


def way_in(db: Session, provider: Provider) -> tuple[str, str]:
    """How the person can have this item do something right now, and the
    state behind that (for the wording only).

    "Can Bevro send it work" is decided exactly as execution decides it
    (`is_available`), so an answer of "direct" is never contradicted by the
    task it starts. A missing credential is checked first: without it the
    work would only fail.
    """
    from app.connect.surfaces import for_provider, has_a_way_to_use
    from app.services.providers import is_available
    from app.services.reconcile import direct_access, state_of

    state = direct_access(db, provider)["state"]
    if state == "needs_credential":
        return BLOCKED, state
    if state == "needs_choice":
        return CHOOSE, state
    # A way in that has stopped answering, or isn't running, is known from
    # its own recent history; whether a helper process is around is
    # execution's call, below.
    if state in ("unreachable", "needs_start"):
        return UNAVAILABLE, state
    if state != "not_set_up":
        if is_available(provider):
            return DIRECT, state
        return UNAVAILABLE, state_of(db, provider).connection
    surfaces = for_provider(provider.surfaces, provider.app_url)
    if any(s.kind == "web_app" for s in surfaces):
        return HANDOFF, state
    if has_a_way_to_use(surfaces) or surfaces:
        return HOW_TO, state
    return SETUP, state


def _message(outcome: str, name: str, sure: bool, state: str | None = None, needs: str | None = None) -> str:
    if outcome == DIRECT:
        return f"{name} can handle this directly." if sure else f"{name} looks like the right one for this."
    if outcome == HANDOFF:
        return f"{name} is the best place for this." if sure else f"{name} looks like the place for this."
    if outcome == CHOOSE:
        return f"{name} can do this once you say which one it's for."
    if outcome == BLOCKED:
        if needs:
            return f"{name} can do this, but direct use in Bevro needs {'an' if needs[:1].lower() in 'aeiou' else 'a'} {needs}."
        return f"{name} can do this, but direct use in Bevro needs a credential."
    if outcome == UNAVAILABLE:
        tail = {
            "needs_start": "but it isn't running right now.",
            "waiting_for_worker": "but the helper on this computer that reaches it isn't running.",
            # Something Bevro ships that nobody has set up is not broken.
            "optional_worker": "but it isn't set up on this installation yet.",
        }.get(state or "", "but I can't reach it right now.")
        return f"{name} is the right one for this, {tail}" if sure else f"{name} looks right for this, {tail}"
    if outcome == HOW_TO:
        return f"{name} is the best place for this. Bevro can't send it work, but here is how you use it."
    if outcome == SETUP:
        return f"{name} fits this, but Bevro doesn't know how to reach it yet."
    return NO_MATCH


def _one(db: Session, provider: Provider, *, sure: bool, why: str | None, rationale: str, source: str = "deterministic") -> Answer:
    outcome, state = way_in(db, provider)
    needs = None
    if outcome == BLOCKED:
        from app.services.reconcile import direct_access

        needs = direct_access(db, provider).get("needs")
    return Answer(
        outcome=outcome,
        message=_message(outcome, provider.name, sure, state, needs),
        sure=sure,
        provider=provider,
        why=why,
        rationale=rationale,
        source=source,
    )


def _choices_message(options: list[Option], asked: bool) -> str:
    if len(options) == 1:
        return f"{options[0].provider.name} could help with this."
    if len(options) == 2:
        return "I found two apps that could help." if not asked else "Two of your apps could help with this."
    return "A few of your apps could help with this."


def resolve(db: Session, request: str, *, chosen: Provider | None = None) -> Answer:
    """The answer to a request. `chosen`: the person already picked the item."""
    from app.services.providers import list_providers

    items = list_providers(db, enabled_only=True)
    by_id = {str(p.id): p for p in items}

    if chosen is not None:
        match = next((m for m in fitting.score(request, [chosen])), None)
        why = fitting.why(match) if match else None
        return _one(db, chosen, sure=True, why=why, rationale="the person chose it", source="explicit")

    fit = fitting.decide(request, items)
    if fit.kind == "one" and fit.best is not None:
        provider = by_id[fit.best.item.id]
        evidence = ", ".join(f"{w}@{f}" for w, f, _ in fit.best.evidence[:6])
        answer = _one(db, provider, sure=fit.sure, why=fitting.why(fit.best), rationale=f"{fit.reason}: {evidence}")
        if answer.outcome == DIRECT and not answer.sure:
            # Thin evidence for something Bevro would run: a routing model, if
            # there is one, may settle it (and may ask a question first).
            opinion = _model_opinion(db, request, by_id)
            if opinion is not None and opinion.outcome != NONE:
                return opinion
            if opinion is not None:
                answer.source = opinion.source  # the model was asked and failed; the words stand
        return answer
    if fit.kind == "choice":
        options = [Option(provider=by_id[m.item.id], summary=fitting.summary(by_id[m.item.id])) for m in fit.choices]
        return Answer(
            outcome=CHOICE,
            message=_choices_message(options, fitting.asks_which_apps(request)),
            choices=options,
            rationale=fit.reason,
        )
    helped = _model_opinion(db, request, by_id)
    if helped is not None and helped.outcome != NONE:
        return helped
    return Answer(outcome=NONE, message=NO_MATCH, rationale=fit.reason, source=helped.source if helped else "deterministic")


def _model_opinion(db: Session, request: str, by_id: dict[str, Provider]) -> Answer | None:
    """When the words settle nothing, or only thinly, and a routing model is
    configured, it may propose one of the items Bevro can drive now.

    Only names, descriptions and capability labels are sent
    (app.routing.catalogue); the model never sees an item Bevro can't drive,
    so it cannot overrule "open that app" with something reachable. Its
    proposal is never "sure": Home asks before anything runs. None when there
    is no model; an Answer with outcome NONE and source "fallback" when the
    model failed and the words stand.
    """
    from app.routing.router import get_router
    from app.routing.validate import InvalidDecision, validate_decision

    router = get_router()
    if router.name != "llm":
        return None
    try:
        decision = router.route(db, request)
        validated = validate_decision(db, decision)
    except InvalidDecision:
        return Answer(outcome=NONE, message=NO_MATCH, rationale="routing model proposal rejected", source="fallback")
    except Exception:  # noqa: BLE001 - a routing model must never stand between the person and an answer
        log.exception("routing model failed")
        return Answer(outcome=NONE, message=NO_MATCH, rationale="routing model failed", source="fallback")
    if decision.routing_source.value != "llm":
        # The model failed and its own fallback answered: the words stand.
        return Answer(outcome=NONE, message=NO_MATCH, rationale="routing model fell back", source="fallback")
    provider = validated.provider
    if provider is None or str(provider.id) not in by_id:
        return None
    answer = _one(db, provider, sure=False, why=fitting.summary(provider) or None, rationale=decision.rationale or "routing model", source="llm")
    answer.decision = decision
    return answer
