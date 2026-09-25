"""When nothing Bevro can drive fits a request, but an app the person has does.

Routing chooses among things Bevro can send work to, and says "nothing fits"
when none of them does. That is true, and not the whole answer: the person
may have an app for exactly this - one with a screen of its own and no way
in for Bevro. Pointing at it is more useful than a dead end.

This is a suggestion, not a decision. Nothing is run, nothing is sent
anywhere, and it looks only at what Bevro already knows: names, what things
say they are for, and how the person uses them. Looser matching than the
router's is acceptable here for that reason; it is still words, not guesses.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.routing.deterministic import _words

# Ways of asking that mean the same kind of thing as a capability, so that
# "where did I write about X" can meet "search what you know". Kinds of
# work, never particular apps.
_INTENTS: list[tuple[re.Pattern[str], set[str]]] = [
    (re.compile(r"\b(where did i|did i (write|note|save|say)|what did i (write|note|say)|find (my|the) notes?|my notes|i (wrote|noted))\b", re.I), {"search", "knowledge", "note", "learned", "noted"}),
    (re.compile(r"\b(search|look (for|up)|find)\b", re.I), {"search", "find"}),
    (re.compile(r"\b(file|filed|filing|where (is|are|does|do) .{0,40}(document|letter|paper|certificate|form)s?|binders?|archived?)\b", re.I), {"archive", "filing", "document", "finding"}),
]

MIN_SCORE = 2


def _vocabulary(provider: Any) -> set[str]:
    words = _words(provider.name) | _words(provider.description or "")
    for cap in provider.capabilities or []:
        if isinstance(cap, dict):
            words |= _words(str(cap.get("id") or "").replace("_", " ").replace(".", " "))
            words |= _words(str(cap.get("title") or ""))
            words |= _words(str(cap.get("description") or ""))
    return words


def _request_words(request: str) -> set[str]:
    words = _words(request)
    for pattern, extra in _INTENTS:
        if pattern.search(request):
            words |= {w for e in extra for w in _words(e)}
    return words


def suggest(db: Session, request: str) -> dict[str, Any] | None:
    """{"id", "name"} of the one app worth pointing at, or None.

    Only things the person can use somewhere of their own (a web app, a chat
    bot) and that Bevro cannot send this work to right now. Its own name in
    the request is enough on its own; otherwise two words must meet, and a
    tie is no answer.
    """
    from app.connect.surfaces import for_provider, has_a_way_to_use
    from app.services.providers import list_providers
    from app.services.reconcile import direct_access

    asked = _request_words(request)
    named = _words(request)
    scored: list[tuple[int, Any]] = []
    for provider in list_providers(db, enabled_only=True):
        if not has_a_way_to_use(for_provider(provider.surfaces, provider.app_url)):
            continue
        if direct_access(db, provider)["state"] == "ready":
            continue  # the router would have chosen it if it fitted
        own_name = _words(provider.name)
        score = len(asked & _vocabulary(provider)) + (MIN_SCORE if own_name and own_name <= named else 0)
        if score >= MIN_SCORE:
            scored.append((score, provider))
    scored.sort(key=lambda pair: -pair[0])
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    best = scored[0][1]
    return {"id": str(best.id), "name": best.name}
