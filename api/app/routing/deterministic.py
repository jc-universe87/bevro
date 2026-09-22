"""Rule-based routing: the offline default, the fallback, and the test double.

Keyword families map to *capabilities*, never to particular providers. A
request that matches nothing gets no provider; Bevro does not route
everything to Research.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.routing.catalogue import CatalogueEntry, build_catalogue
from app.routing.decision import InputRequestSpec, RoutingDecision, RoutingSource

# (capability id, pattern). First match wins; order matters: a coding request
# that mentions an event is still a coding request.
RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "coding",
        re.compile(
            r"\b(code|coding|bug|bugs|fix|fixes|fixing|implement\w*|refactor\w*|tests?|unit test|typescript|python|javascript|"
            r"css|component|endpoint|api route|migration|repository|repo|commit|function|class|module|frontend|backend|"
            r"ui|layout|spacing|button|page|stylesheet|lint\w*|build (fails|error)|compile|stack ?trace|exception|"
            r"pull request|merge|branch|dependency|dependencies|package)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "events.allocate",
        re.compile(
            r"\b(allocat\w*|participants?|attendees?|events?|sessions?|registrations?|seating|"
            r"tickets?|delegates?|conference|workshop|retreat|roster|timetable)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "research",
        re.compile(
            r"\b(research|compare|comparison|analy[sz]e|analysis|find|look ?up|look into|investigate|options?|alternatives?|"
            r"differences?|pros and cons|recommend\w*|guidance|evidence|review|summari[sz]e|overview|which|what|how|why|"
            r"should|best|versus|vs\.?)\b",
            re.IGNORECASE,
        ),
    ),
]


# A request that *opens* with a research verb is a research request even if it
# goes on to mention code: "Compare approaches for implementing MCP support."
_LEADING_RESEARCH = re.compile(
    r"^\W*(please\s+)?(research|compare|analy[sz]e|investigate|look into|look up|find out|summari[sz]e|review|explain|"
    r"what|which|how|why|should|is|are)\b",
    re.IGNORECASE,
)


def wanted_capability(request: str) -> str | None:
    if _LEADING_RESEARCH.match(request):
        return "research"
    for capability, pattern in RULES:
        if pattern.search(request):
            return capability
    return None


def _has(entry: CatalogueEntry, capability: str) -> bool:
    return any(c.id == capability for c in entry.capabilities)


# Words too common to tell providers apart.
_STOPWORDS = frozenset(
    "the a an and or for with about from into onto over this that these those what which who whom whose when where why how "
    "should could would will can may might must please me my our your their its let us we you they them then than also just "
    "some any all each every both either neither not no yes very more most much many few little less least well good best".split()
)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")


def _stem(word: str) -> str:
    """Just enough to let "competitors" meet "competitor"; no real stemming."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("es") and not word.endswith("ses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def _mention_score(request_words: set[str], entry: CatalogueEntry) -> int:
    """How many distinct request words name this provider or one of its capabilities.
    Names and capability ids/titles only: descriptions are too loose."""
    vocabulary: set[str] = _words(entry.name)
    for cap in entry.capabilities:
        vocabulary |= _words(cap.id.replace("_", " ").replace(".", " "))
        vocabulary |= _words(cap.title or "")
    return len(request_words & vocabulary)


def _prefer_mentioned(request: str, entries: list[CatalogueEntry]) -> CatalogueEntry:
    """Among providers that all fit, the one the request actually names wins; otherwise catalogue order."""
    words = _words(request)
    return max(entries, key=lambda e: _mention_score(words, e)) if len(entries) > 1 else entries[0]


UNAVAILABLE_PREFIX = "unavailable:"  # reason = "unavailable:<capability>" when a provider exists but cannot run now

# Plain wording per capability when its providers exist but are not available.
UNAVAILABLE_MESSAGES = {
    "coding": "Coding help isn't set up on this installation yet.",
}


class DeterministicRouter:
    name = "deterministic"

    def decide(self, request: str, catalogue: list[CatalogueEntry]) -> RoutingDecision:
        """`catalogue` may include unavailable entries; they are never selected."""
        capability = wanted_capability(request)
        usable = [e for e in catalogue if e.available and e.can_invoke]
        if capability is not None:
            fitting = [e for e in usable if _has(e, capability)]
            if fitting:
                return self._select(_prefer_mentioned(request, fitting), f"request matched the {capability!r} rule", 0.5)
            if any(_has(entry, capability) for entry in catalogue):
                return RoutingDecision(routing_source=RoutingSource.DETERMINISTIC, reason=f"{UNAVAILABLE_PREFIX}{capability}")
        # No keyword family fits (or nothing declares it): a provider the request
        # names outright, by its name or a capability it declares, still counts.
        # Connected agents become reachable this way without a rule for each.
        words = _words(request)
        scored = sorted(((_mention_score(words, e), e) for e in usable), key=lambda pair: -pair[0])
        if scored and scored[0][0] >= 2:
            return self._select(scored[0][1], f"request names {scored[0][1].id!r} or its capabilities", 0.35)
        if capability is not None:
            return RoutingDecision(routing_source=RoutingSource.DETERMINISTIC, reason=f"no provider declares {capability!r}")
        return RoutingDecision(routing_source=RoutingSource.DETERMINISTIC, reason="no rule matched the request")

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
        capability = decision.reason[len(UNAVAILABLE_PREFIX):]
        return UNAVAILABLE_MESSAGES.get(capability, "The provider for that isn't available on this installation right now.")
    return None
