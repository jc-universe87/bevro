"""Which of the person's apps and agents fits a request, and how sure that is.

This answers "what is the right tool?" and nothing else. Whether Bevro can
use that tool right now - directly, by opening its app, after a credential -
is a separate question (`app.routing.resolve`), asked afterwards, so that an
app which happens to be reachable never beats one that actually fits.

Everything here is words Bevro already holds about each item: its name, what
the person said it is for, the capabilities it declares, its description and
what discovery read about it. Nothing is fetched, nothing is sent anywhere,
and no item is named in this file.

How a request meets an item
---------------------------
Each word of the request is looked for in the item's text, and counts for as
much as the best place it was found:

    name                          3.0   the person is naming it
    what the person said it's for 3.0   their words, not a guess
    capability names and terms    2.5   the item's own word for a thing it does
    capability titles             2.0
    description                   1.5
    capability descriptions       1.0
    what discovery read           1.0   README text and the like

and is then worth less when it is a broad word ("find", "review", "report")
or when several items share it: a word everyone uses tells them apart from
nothing. A handful of reusable intent concepts add the words a request means
without saying ("where did I write about..." means notes and knowledge);
those count for less than words the person actually used.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# --------------------------------------------------------------------------- words

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]{1,}")

_STOPWORDS = frozenset(
    "the a an and or for with about from into onto over this that these those what which who whom whose when where why how "
    "should could would will can may might must please me my our your their its let us we you they them then than also just "
    "some any all each every both either neither not no yes very more most much many few little less least well good "
    "is are was were be been being do does did done have has had it i am to of in on at by as so if up out there here "
    "i'd i'm i've it's there's that's don't can't won't".split()
)

# Say something about almost any piece of work, so they settle little.
BROAD = frozenset(
    "research review analysis analyse analyze find search look help manage work check show get make run create write read "
    "need want thing something information data new current latest change changes update list open use app agent tool "
    "question answer idea plan organise organize prepare overview summary summarise summarize compare option best "
    "report file note project stuff item today week month year time "
    # Filler: sizes, counts and order describe the request, not the kind of work.
    "small large big simple quick easy short long one two three four five first last next other same own whole".split()
)

# How a thing is built, not what it is for. Ignored in what discovery read.
TECHNICAL = frozenset(
    "docker compose fastapi flask django python node http https api rest json yaml database postgres postgresql sqlite "
    "redis server endpoint endpoints container kubernetes uvicorn react vite typescript javascript npm pip cli stdio mcp "
    "openapi swagger localhost port config env repo github git backend frontend module modules library framework thin "
    "control wrapper service services route routes schema schemas deterministic phase implements implementation exposes "
    "existing layer lightweight metadata directory directories".split()
)

# Words in a name that say what kind of thing it is, not which one.
_NAME_FILLER = frozenset("agent agents app apps assistant bot tool tools service api control the my".split())


def stem(word: str) -> str:
    """Just enough to let "notes" meet "noted", "filing" meet "file" and
    "competitors" meet "competitor". Not real stemming, and consistent: every
    form of a word ends up the same, even if what it ends up as is not a word."""
    w = word.strip("'-")
    if w.endswith("'s"):
        w = w[:-2]
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if not w.endswith(suffix) or len(w) - len(suffix) < 3:
            continue
        if suffix == "es" and w.endswith("ses"):
            continue
        if suffix == "s" and w.endswith("ss"):
            continue
        base = w[: len(w) - len(suffix)] + keep
        # scanned -> scan, planning -> plan; filling keeps its double l.
        if suffix in ("ed", "ing") and len(base) >= 4 and base[-1] == base[-2] and base[-1] not in "lsz":
            base = base[:-1]
        w = base
        break
    return w[:-1] if len(w) > 3 and w.endswith("e") else w


def words(text: Any, *, drop: frozenset[str] = frozenset()) -> set[str]:
    raw = _WORD_RE.findall(str(text or "").lower().replace("’", "'").replace("_", " ").replace(".", " "))
    return {stem(w) for w in raw if w not in _STOPWORDS and w not in drop and len(w) > 1}


_BROAD = {stem(w) for w in BROAD}
_TECHNICAL = frozenset(TECHNICAL)

# --------------------------------------------------------------------------- intent concepts

# Kinds of work people ask for without using an item's words for them. Each
# adds the words the request means; none of them names an app.
CONCEPTS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "knowledge",
        re.compile(
            r"\b((where|what|when) did i( \w+ly)? (write|note|jot|put down|save|say|mention|read|conclude|decide|think|learn)|"
            r"did i( \w+ly)? (write|note|save|say)|i( \w+ly)? (wrote|noted|concluded|decided|learned|learnt)|"
            r"(my|previous|earlier|past) (notes?|thoughts|thinking|ideas|journal|conclusions?|reflections?))\b",
            re.I,
        ),
        "knowledge note noted learned written thinking",
    ),
    (
        "filing",
        re.compile(
            r"\b(filed|filing|scan|scanned|binders?|archived?|paperwork|where (is|are|did i put|have i put|do i keep) (my|the|our)|"
            r"passport|certificate|receipt|contract|insurance polic\w*|bank statement)\b",
            re.I,
        ),
        "archive filing document binder scan",
    ),
    (
        "career",
        re.compile(
            r"\b(jobs?|careers?|vacanc\w*|job applications?|appl(y|ying|ied) (for|to)|interviews?|cv|r[eé]sum[eé]|cover letters?|"
            r"recruit\w*|hiring|openings?|opportunit\w*|employers?)\b",
            re.I,
        ),
        "career job vacancy application opportunity",
    ),
    (
        "market",
        re.compile(r"\b(trends?|competitors?|competition|competitive|market|industry|landscape|rivals?)\b", re.I),
        "market competitor trend research",
    ),
    (
        "coding",
        re.compile(
            r"\b(code|coding|bugs?|refactor\w*|implement\w*|(add|write|run|fix)\w* (a |an |the |some )?(unit |failing )?tests?|"
            r"unit tests?|failing tests?|flaky tests?|typescript|javascript|css|functions?|modules?|components?|endpoints?|"
            r"migrations?|frontend|backend|stack ?trace|exception|pull request|repository|compile|lint\w*)\b",
            re.I,
        ),
        "coding code bug debugging software",
    ),
    (
        "scheduling",
        re.compile(r"\b(book|booking|appointments?|calendar|meetings?|reschedul\w*|diary)\b", re.I),
        "schedule calendar booking appointment",
    ),
    (
        "email",
        re.compile(r"\b(e-?mails?|inbox|mailbox)\b", re.I),
        "email inbox",
    ),
    (
        "events",
        re.compile(r"\b(allocat\w*|participants?|attendees?|seating|delegates?|conference|registrations?)\b", re.I),
        "events allocate participant",
    ),
]

# "What apps can help me with X?" asks for a list, not for the work.
_WHICH_APPS = re.compile(
    r"\b(what|which)\s+(of\s+my\s+)?(apps?|agents?|tools?|ones?)\b.{0,30}\b(can|could|would|will|do|does|should|help|for)\b|"
    r"\b(is there|do i have)\s+(an?\s+)?(app|agent|tool)\b",
    re.I,
)

LITERAL = 1.0
IMPLIED = 0.6

# "Compare these", "please find...", "can you summarise...": the verb a request
# opens with is what it asks to be done, and meeting an item's own verb for
# what it does is evidence even when the verb is a broad one.
_OPENING = re.compile(r"^\W*(?:(?:please|can you|could you|would you|help me|i want to|i need to|i'd like to)\s+)*([a-z]+)", re.I)


def opening_verb(request: str) -> str | None:
    m = _OPENING.match(request)
    if not m or m.group(1).lower() in _STOPWORDS:
        return None
    return stem(m.group(1).lower())


def request_terms(request: str) -> tuple[dict[str, float], list[str]]:
    """{word: how directly the person said it}, and the concepts that were recognised."""
    terms = {w: LITERAL for w in words(request)}
    seen: list[str] = []
    for name, pattern, adds in CONCEPTS:
        if pattern.search(request):
            seen.append(name)
            for w in words(adds):
                terms.setdefault(w, IMPLIED)
    # Asking which app to use is not asking for apps.
    for w in ("app", "agent", "tool"):
        terms.pop(w, None)
    return terms, seen


def asks_which_apps(request: str) -> bool:
    return bool(_WHICH_APPS.search(request))


# --------------------------------------------------------------------------- items

FIELD_WEIGHTS: dict[str, float] = {
    "name": 3.0,
    "purpose": 3.0,
    "capability": 2.5,
    "title": 2.0,
    "description": 1.5,
    "detail": 1.0,
    "source": 1.0,
}

MAX_SOURCE_TEXT = 1200


@dataclass
class Profile:
    """What Bevro knows about one item, as words, by where they came from."""

    id: str
    name: str
    fields: dict[str, set[str]]
    name_words: set[str]
    # Readable labels for "why": the matched capability and what it is about.
    labels: dict[str, list[tuple[str, str | None, bool]]] = field(default_factory=dict)
    description: str = ""

    @property
    def vocabulary(self) -> set[str]:
        out: set[str] = set()
        for ws in self.fields.values():
            out |= ws
        return out

    @property
    def main_words(self) -> set[str]:
        """The words it leads with. Fine print (capability details, README
        text) is evidence for it, but does not make a word common."""
        out: set[str] = set()
        for f, ws in self.fields.items():
            if FIELD_WEIGHTS[f] >= 1.5:
                out |= ws
        return out


def profile(item: Any) -> Profile:
    """Built from a Provider row (or anything shaped like one)."""
    fields: dict[str, set[str]] = {k: set() for k in FIELD_WEIGHTS}
    labels: dict[str, list[tuple[str, str | None, bool]]] = {}
    # A word explains best the capability it names; one it merely mentions comes after.
    later: dict[str, list[tuple[str, str | None, bool]]] = {}
    name_words = words(item.name, drop=_NAME_FILLER)
    fields["name"] = set(name_words)
    for cap in item.capabilities or []:
        if not isinstance(cap, dict) or not cap.get("id"):
            continue
        by_person = cap.get("by") == "person"
        own = words(cap.get("id")) | words(cap.get("subject")) | set().union(*(words(t) for t in cap.get("terms") or [] if isinstance(t, str)))
        title = words(cap.get("title")) | words(cap.get("action"))
        if by_person:
            fields["purpose"] |= own | title
        else:
            fields["capability"] |= own
            fields["title"] |= title
        fields["detail"] |= words(cap.get("description"), drop=_TECHNICAL)
        label = (str(cap.get("title") or cap.get("id")), cap.get("subject"), by_person)
        named_by = words(cap.get("id")) | words(cap.get("subject"))
        for w in named_by:
            labels.setdefault(w, []).append(label)
        for w in (own | title) - named_by:
            later.setdefault(w, []).append(label)
    for w, more in later.items():
        labels.setdefault(w, []).extend(more)
    fields["description"] = words(item.description)
    fields["source"] = words(str(getattr(item, "source_description", None) or "")[:MAX_SOURCE_TEXT], drop=_TECHNICAL)
    return Profile(id=str(item.id), name=item.name, fields=fields, name_words=name_words, labels=labels, description=" ".join(str(item.description or "").split()))


# --------------------------------------------------------------------------- scoring

@dataclass
class Match:
    item: Profile
    # Ranking: how well this item, rather than another, fits.
    score: float
    # (word, field) pairs that counted, strongest first. Internal: never shown as-is.
    evidence: list[tuple[str, str, float]]
    named: bool = False
    # Evidence at all, before shared words are discounted: a word two items
    # share tells them apart from nothing, but is still evidence for both.
    raw: float = 0.0

    def matched_labels(self) -> list[tuple[str, str | None, bool]]:
        out: list[tuple[str, str | None, bool]] = []
        # The one capability each word says most about, strongest word first.
        for word, _field, _points in self.evidence:
            label = next(iter(self.item.labels.get(word, [])), None)
            if label is not None and label not in out:
                out.append(label)
        return out


NAMED_BONUS = 3.0
BROAD_WORTH = 0.4
VERB = 0.7


def score(request: str, items: Iterable[Any]) -> list[Match]:
    """Every item that the request meets at all, best first."""
    terms, _concepts = request_terms(request)
    literal = {w for w, how in terms.items() if how == LITERAL}
    verb = opening_verb(request)
    profiles = [profile(i) for i in items]
    # How many items use each word: a word they all use distinguishes none of them.
    sharing: dict[str, int] = {}
    for p in profiles:
        for w in p.main_words:
            sharing[w] = sharing.get(w, 0) + 1
    out: list[Match] = []
    for p in profiles:
        total = 0.0
        raw = 0.0
        evidence: list[tuple[str, str, float]] = []
        for w, how in terms.items():
            where = [f for f, ws in p.fields.items() if w in ws]
            if not where:
                continue

            def worth(f: str) -> float:
                if w not in _BROAD:
                    return FIELD_WEIGHTS[f]
                # A broad verb the request opens with, where the item says in
                # a sentence what it is for, is what the item does; anywhere
                # else ("Write code" for "write a poem") it is a coincidence.
                return FIELD_WEIGHTS[f] * (VERB if w == verb and f in ("description", "purpose") else BROAD_WORTH)

            best = max(where, key=worth)
            points = worth(best) * how / math.sqrt(sharing.get(w, 1))
            total += points
            raw += worth(best) * how
            evidence.append((w, best, points))
        named = bool(p.name_words) and p.name_words <= literal
        if named:
            total += NAMED_BONUS
            raw += NAMED_BONUS
        if total > 0:
            evidence.sort(key=lambda e: -e[2])
            out.append(Match(item=p, score=round(total, 3), evidence=evidence, named=named, raw=round(raw, 3)))
    out.sort(key=lambda m: -m.score)
    return out


# --------------------------------------------------------------------------- confidence

# Below this, a match is a coincidence of words.
MIN_SCORE = 1.0
# At or above this, with a clear lead, Bevro is sure enough to act.
SURE_SCORE = 2.5
SURE_LEAD = 1.5
# A runner-up this close is a real alternative, and the person chooses.
CLOSE = 0.7
MAX_CHOICES = 3


@dataclass
class Fit:
    """ "one" (sure or not), "choice", or "none"."""

    kind: str
    best: Match | None = None
    sure: bool = False
    choices: list[Match] = field(default_factory=list)
    reason: str = ""


def decide(request: str, items: Iterable[Any]) -> Fit:
    ranked = [m for m in score(request, items) if m.raw >= MIN_SCORE]
    if not ranked:
        return Fit(kind="none", reason="nothing the person has is described in these terms")
    if asks_which_apps(request):
        return Fit(kind="choice", choices=ranked[:MAX_CHOICES], reason="asked which apps could help")
    top = ranked[0]
    close = [m for m in ranked[1:] if m.score >= CLOSE * top.score]
    # Naming an item outright settles it, unless the request names another too.
    if top.named and not any(m.named for m in ranked[1:]):
        return Fit(kind="one", best=top, sure=True, reason="named")
    if close:
        return Fit(kind="choice", choices=[top, *close][:MAX_CHOICES], reason="several fit about as well")
    runner = ranked[1].score if len(ranked) > 1 else 0.0
    sure = top.score >= SURE_SCORE and top.score >= SURE_LEAD * runner
    return Fit(kind="one", best=top, sure=sure, reason="clear lead" if sure else "best, but thinly evidenced")


# --------------------------------------------------------------------------- plain words

def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def why(match: Match) -> str:
    """One plain sentence on why this item fits. No scores, no mechanics."""
    labels = match.matched_labels()
    theirs = [t for t, _s, by_person in labels if by_person]
    if theirs:
        return f"You said {match.item.name} is for {_join([t.lower() for t in theirs[:3]])}."
    subjects: list[str] = []
    for _t, subject, _p in labels:
        if subject and subject.lower() not in subjects:
            subjects.append(subject.lower())
    if subjects:
        return f"{match.item.name} works with {_join(subjects[:3])}."
    if match.item.description:
        return match.item.description if match.item.description.endswith(".") else match.item.description + "."
    if labels:
        return f"{match.item.name} is for {_join([t.lower() for t, _s, _p in labels[:3]])}."
    return f"Its description matches what you asked for."


def summary(item: Any) -> str:
    """A short line saying what an item is for, for a list of choices."""
    text = " ".join(str(item.description or "").split())
    if text:
        return text if len(text) <= 120 else text[:117].rsplit(" ", 1)[0] + "…"
    titles = [str(c.get("title") or c.get("id")) for c in item.capabilities or [] if isinstance(c, dict) and c.get("id")]
    return _join([t for t in titles[:3]]) if titles else ""
