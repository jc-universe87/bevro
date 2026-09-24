"""Turning what a service exposes into something a person would say.

A service names things the way its own code does: a tag called `vacancies`,
a route called `/opportunities`, an operation summarised as "Record YES /
MAYBE / NO / SKIP". None of that is what someone wants to read when they are
deciding whether this can help them.

This module goes from that evidence to a short phrase - "review
opportunities", "prepare documents", "run searches" - using grammar and
counting rather than knowledge of any particular service. Two questions:

    what is being worked on    the group's own noun, and the nouns its
                               routes use, whichever the service leans on
    what is being done to it   the verbs its operation summaries use, and
                               how much of its work changes anything

Everything below is ordinary English vocabulary. No entry knows what any
product is, and nothing here is allowed to: a careers tool, an issue tracker
and a finance system must all come out the other side sounding like
themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# --------------------------------------------------------------------------- verbs

# Each canonical action, with the words that evidence it and the two forms a
# sentence needs. Ordered strongest-first: when two actions are equally
# evidenced, the one nearer the top is the one a person came for.
VERB_FORMS: list[tuple[str, str, str, frozenset[str]]] = [
    # canonical, base form, third person, the words that point at it
    ("find", "find", "finds", frozenset("search searches searching find finds finding look lookup query match matches matching discover scan seek".split())),
    ("review", "review", "reviews", frozenset("review reviews reviewing evaluate evaluates evaluated assess assesses decide decides decision decisions approve approves approval reject rejects rate rates score scores judge shortlist shortlists record records".split())),
    ("convert", "convert", "converts", frozenset("convert converts converting transform transforms translate translates parse parses export exports import imports extract extracts".split())),
    ("prepare", "prepare", "prepares", frozenset("prepare prepares preparing generate generates generated render renders compose composes draft drafts write writes produce produces assemble build builds".split())),
    ("run", "run", "runs", frozenset("run runs running execute executes executed start starts trigger triggers launch launches perform performs process processes".split())),
    ("send", "send", "sends", frozenset("send sends sending notify notifies notification email emails publish publishes deliver delivers share shares".split())),
    ("track", "track", "tracks", frozenset("track tracks tracking monitor monitors monitoring watch watches follow follows progress alert alerts observe".split())),
    ("create", "create", "creates", frozenset("create creates creating add adds new register registers submit submits upload uploads open opens insert".split())),
    ("manage", "manage", "manages", frozenset("update updates updating edit edits editing change changes modify modifies set sets rename renames delete deletes remove removes archive archives manage manages store stores save saves replace replaces".split())),
    ("report", "report on", "reports on", frozenset("report reports reporting summarise summarises summarize summarizes statistics stats counts count metrics breakdown funnel analytics analyse analyses analyze analyzes".split())),
    ("show", "show", "shows", frozenset("list lists listing get gets show shows read reads fetch fetches return returns view views display displays retrieve every all".split())),
]
# The words that *are* each action, as against the words that merely point at
# it. "Statistics" is a reason to think a group reports; "reports" is the
# word "report" wearing a noun's clothes. A subject of the second kind makes
# the phrase say one thing twice - "tracks monitors", "finds searches" - and
# a subject of the first kind does not: "reports on statistics" is fine.
OWN_FORMS: dict[str, frozenset[str]] = {
    "find": frozenset("find finds finding search searches searching lookup lookups".split()),
    "review": frozenset("review reviews reviewing evaluation evaluations assessment assessments".split()),
    "convert": frozenset("convert converts converting conversion conversions transform transforms transformation".split()),
    "prepare": frozenset("prepare prepares preparing preparation preparations".split()),
    "run": frozenset("run runs running execution executions process processes processing".split()),
    "send": frozenset("send sends sending notification notifications".split()),
    "track": frozenset("track tracks tracking monitor monitors monitoring".split()),
    "create": frozenset("create creates creating creation creations".split()),
    "manage": frozenset("manage manages managing management update updates updating edit edits editing".split()),
    "report": frozenset("report reports reporting".split()),
    "show": frozenset("show shows showing view views listing listings".split()),
}

_BY_NAME = {name: (base, third, words) for name, base, third, words in VERB_FORMS}
KNOWN_VERBS = frozenset(_BY_NAME)
_PRIORITY = {name: position for position, (name, *_rest) in enumerate(VERB_FORMS)}
# Everything except plain looking is work someone asked for.
_PASSIVE = frozenset({"show"})

# What a service calls its own machinery rather than its purpose. A group
# named for one of these is ranked below a group that does something.
SUPPORTING = frozenset(
    """profile profiles workspace workspaces artifact artifacts health settings setting config configuration
    auth authentication session sessions token tokens admin meta status system internal debug default general
    user users account accounts version versions""".split()
)

# Nouns for the machinery of doing work rather than for the work. A service
# calls something a "run" because that is what its code does with it; the
# person asked for whatever the run was of. Used to rank, never to exclude:
# when there is nothing better to say, saying this is better than saying
# nothing.
MACHINERY = frozenset("run runs execution executions job jobs workflow workflows process processes batch batches pipeline pipelines queue queues".split())

# ...and the words a person is more likely to have in mind. A service that
# mentions any of these is naming what its machinery is for.
PURPOSE = frozenset(
    "search searches result results report reports analysis analyses activity activities summary summaries insight insights finding findings".split()
)

# Path segments that name no subject at all.
_NOT_A_SUBJECT = frozenset({"api", "v1", "v2", "v3", "rest", "public", "internal", "index", "id", "by"})
_WORD = re.compile(r"[a-z][a-z-]{2,}")
# "stats" and "statistics" are the same thing said twice.
_SAME_THING = {"stats": "statistics", "analytics": "statistics", "metrics": "statistics", "docs": "documents"}


@dataclass(frozen=True)
class Phrase:
    """One thing this can do, in words, with how much it matters."""

    verb: str        # "review"
    subject: str     # "opportunities"
    weight: float
    # A second verb the group evidenced almost as strongly: "creates *and
    # manages* projects". Left out when nothing came close.
    also: str | None = None

    @property
    def action(self) -> str:
        """"review opportunities" - the base form, for a list."""
        return f"{_BY_NAME[self.verb][0]} {self.subject}".strip()

    @property
    def title(self) -> str:
        """"Review opportunities" - for the "Can" list."""
        return self.action[0].upper() + self.action[1:]

    def said_of(self, subject: str) -> str:
        """"reviews opportunities" - third person, for a sentence."""
        return f"{_BY_NAME[self.verb][1]} {subject}".strip()


# --------------------------------------------------------------------------- reading the evidence

def _words_of(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def _subject_from(tag: str, operations: list[dict[str, Any]]) -> str:
    """What this group of operations is about.

    The tag is the service's own name for the group, but its routes often
    use a different word for the same thing - a group tagged `shortlist`
    whose every path says `/opportunities`. When the routes agree among
    themselves and disagree with the tag, they win: a route is what the
    service actually calls the thing.
    """
    tag_word = _SAME_THING.get(tag.lower(), tag.lower().replace("_", " ").strip())
    counted: dict[str, float] = {}
    for operation in operations:
        seen: set[str] = set()
        for segment in str(operation.get("path") or "").split("/"):
            segment = segment.strip().lower()
            if not segment or segment.startswith("{") or segment in _NOT_A_SUBJECT or re.fullmatch(r"v\d+", segment):
                continue
            word = _SAME_THING.get(segment, segment.replace("_", " ").replace("-", " "))
            if len(word) < 4 or word in seen:
                continue
            seen.add(word)
            counted[word] = counted.get(word, 0) + 1.0
        # A summary is prose rather than a name, so it counts for less - but
        # it is where a service says what its machinery is actually for.
        for word in _words_of(operation.get("summary")):
            if word in PURPOSE:
                counted[word] = counted.get(word, 0) + 0.5

    if not counted:
        return tag_word
    # The service's own name for the group wins whenever its routes use it
    # too - unless that name is for the machinery, in which case something
    # that says what the machinery is for is worth more.
    if counted.get(tag_word, 0) * 2 >= len(operations) and tag_word not in MACHINERY:
        return tag_word

    best = max(counted, key=lambda word: (_as_a_subject(word, counted[word]), -len(word)))
    if _as_a_subject(best, counted[best]) <= 0:
        return tag_word
    if best == tag_word or counted[best] * 2 >= len(operations) or best in PURPOSE:
        return _plural(best)
    return tag_word


def _as_a_subject(word: str, count: float) -> float:
    """How much this word is worth as the thing a person is asking about."""
    if word in SUPPORTING:
        return -1.0
    score = count
    if word in PURPOSE:
        score += 2.0
    if word in MACHINERY:
        score -= 3.0
    # A word that is only ever a verb names no thing: "execute", "validate".
    if not _could_be_a_noun(word):
        score -= 3.0
    return score


def _could_be_a_noun(word: str) -> bool:
    """Could someone ask for this thing, or is it only ever something done?

    "Documents" and "monitors" name collections; "execute" and "validate"
    name no thing at all. A plural is the giveaway, and a handful of words
    are plainly both.
    """
    if word in PURPOSE or word.endswith("s"):
        return True
    return not any(word in words for _n, _b, _t, words in VERB_FORMS)


def _plural(word: str) -> str:
    if word.endswith("s") or " " in word:
        return word
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + ("es" if word.endswith(("s", "x", "ch", "sh")) else "s")


def _verbs_from(operations: list[dict[str, Any]], subject: str) -> tuple[str, str | None]:
    """What this group does with it, by weight of evidence.

    Summaries are what the service chose to say; a path word counts for less
    because it is a name rather than a sentence. Where two actions are
    evidenced equally, the one a person is more likely to have come for wins.
    """
    votes: dict[str, float] = {}
    # A service that says "monitor" about a thing it calls a monitor has told
    # Bevro what the thing is, not what is done with it. Its own name never
    # votes for the verb, or every group would describe itself in a circle.
    itself = {subject, _singular(subject), _plural(_singular(subject))}
    for operation in operations:
        for word in _words_of(operation.get("summary")):
            if word in itself:
                continue
            for name, _base, _third, evidence in VERB_FORMS:
                if word in evidence:
                    votes[name] = votes.get(name, 0.0) + 1.0
        for segment in _words_of(str(operation.get("path") or "").replace("/", " ")):
            if segment in itself:
                continue
            for name, _base, _third, evidence in VERB_FORMS:
                if segment in evidence:
                    votes[name] = votes.get(name, 0.0) + 0.5
        # A method is the weakest evidence there is, and only used when the
        # service said nothing else about what the operation is for.
        if not operation.get("summary"):
            votes["create" if str(operation.get("method")).upper() == "POST" else "show"] = votes.get("create", 0.0) + 0.25
    # A verb that is the subject said differently - "run runs" - describes
    # nothing. Whatever the evidence, that is not the sentence.
    for name in [n for n in votes if _says_the_same(n, subject)]:
        votes.pop(name)
    if not votes:
        # Nothing said what this is for, so the method is all there is. The
        # same rule still applies: whatever is chosen must not be the subject
        # said again, or a group called "creations" would create creations.
        makes = any(str(op.get("method")).upper() == "POST" for op in operations)
        for fallback in (("create", "manage", "show") if makes else ("show", "manage", "create")):
            if not _says_the_same(fallback, subject):
                return fallback, None
        return "show", None
    ranked = sorted(votes.items(), key=lambda pair: (-pair[1], _PRIORITY[pair[0]]))
    best, best_votes = ranked[0]
    # A runner-up worth saying is one the service kept mentioning too.
    runner_up = next(
        (
            name
            for name, count in ranked[1:]
            if count >= best_votes * 0.6
            and name not in _PASSIVE
            and not _reads_oddly(best, name)
            and not _much_the_same(best, name)
        ),
        None,
    )
    return best, runner_up


def _says_the_same(verb: str, subject: str) -> bool:
    """Is this verb just the subject again, in the place a verb should be?

    "Runs runs" is the obvious case. "Tracks monitors" and "manages
    management" are the same mistake wearing different endings: the word
    that named the thing is the word being used to describe doing something
    to it, so the phrase says nothing twice. A verb whose own evidence
    includes the subject is describing the subject.
    """
    base, third, _words = _BY_NAME[verb]
    head = subject.split(" ")[0].lower()
    own = OWN_FORMS.get(verb, frozenset())
    for form in {head, _singular(head)}:
        if form in own or form in (base, third, f"{base}s", base.rstrip("e")):
            return True
        # "manage" and "management"; "process" and "processing".
        if len(base) >= 4 and (form.startswith(base) or base.startswith(form[:max(4, len(form) - 3)])):
            return True
    return False


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses") or word.endswith("ches") or word.endswith("shes"):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _weight_of(tag: str, operations: list[dict[str, Any]], verb: str) -> float:
    """How much of a reason this group is to have connected the thing at all.

    Work that changes something, or produces something, is what someone came
    for. Reading a list is useful but it is not the point, and a group named
    after the machinery is a further step down.
    """
    if not operations:
        return 0.0
    doing = sum(2.0 if str(op.get("safety")) == "work_execution" else 1.0 if str(op.get("safety")) == "state_change" else 0.0 for op in operations)
    score = (doing / (2.0 * len(operations))) * 3.0
    score += min(len(operations), 6) * 0.15
    if verb not in _PASSIVE:
        score += 1.0
    if tag.lower() in SUPPORTING or _SAME_THING.get(tag.lower(), tag.lower()) in SUPPORTING:
        score -= 1.5
    return score


def phrase_for(tag: str, operations: list[dict[str, Any]]) -> Phrase:
    """One group of operations, as a thing a person can ask for."""
    subject = _subject_from(tag, operations)
    verb, also = _verbs_from(operations, subject)
    weight = _weight_of(tag, operations, verb)
    if _settled_for_less(operations, subject, verb):
        # Its own best word for itself was its own name again. What is left
        # is true but says less, so it gives way to a group that says more.
        weight -= 0.5
    return Phrase(verb=verb, subject=subject, weight=weight, also=also)


def _settled_for_less(operations: list[dict[str, Any]], subject: str, verb: str) -> bool:
    return any(_says_the_same(name, subject) for name, *_rest in VERB_FORMS if name != verb and _evidenced(name, operations))


def _evidenced(verb: str, operations: list[dict[str, Any]]) -> bool:
    words = _BY_NAME[verb][2]
    return any(word in words for op in operations for word in _words_of(op.get("summary")))


# --------------------------------------------------------------------------- saying it

# Pairs of actions that are one action said twice. Naming both adds a word
# and no meaning: "prepares and creates documents" is just "prepares".
_MUCH_THE_SAME = (frozenset({"prepare", "create"}), frozenset({"show", "report"}), frozenset({"find", "show"}))


def _much_the_same(first: str, second: str) -> bool:
    return frozenset({first, second}) in _MUCH_THE_SAME


def _reads_oddly(first: str, second: str) -> bool:
    """Two verbs that share a form read as one word repeated."""
    return len({_BY_NAME[first][0], _BY_NAME[first][1], _BY_NAME[second][0], _BY_NAME[second][1]}) < 4


def _capitalised(sentence: str) -> str:
    return sentence[0].upper() + sentence[1:] if sentence else sentence


def sentence_from(phrases: Iterable[Phrase], *, limit: int = 140, most: int = 3, per_clause: int = 2) -> str:
    """Several things it can do, as one sentence someone would say.

    Two actions on the same subject become one clause - "finds and reviews
    opportunities" - because that is how it would be said out loud.
    """
    # Two things said about one subject become one clause - "finds and
    # reviews opportunities" - and one thing said about two subjects becomes
    # one as well - "reviews applications and opportunities". Both are how it
    # would be said out loud, and both make room for another clause.
    by_subject: dict[str, list[Phrase]] = {}
    for phrase in phrases:
        by_subject.setdefault(phrase.subject, []).append(phrase)

    parts: list[tuple[str, str | None, str]] = []  # (verb, second verb, subject)
    for subject, group in by_subject.items():
        best = group[0]
        second = next((p.verb for p in group[1:] if p.verb != best.verb and not _reads_oddly(best.verb, p.verb)), best.also)
        parts.append((best.verb, second, subject))

    clauses: list[str] = []
    taken: set[str] = set()
    for verb, _second, _subject in parts:
        if verb in taken:
            continue
        taken.add(verb)
        together = [(sec, sub) for v, sec, sub in parts if v == verb][:per_clause]
        subjects = [sub for _sec, sub in together]
        # Two verbs only when every subject in the clause earned both.
        shared = {sec for sec, _sub in together}
        also = shared.pop() if len(shared) == 1 else None
        # Both verbs agree with the subject - "converts and prepares
        # documents" - and neither may be a phrase, because "reports on and
        # creates incidents" is not something anyone says.
        pair = also and " " not in _BY_NAME[verb][1] and " " not in _BY_NAME[also][1]
        said = f"{_BY_NAME[verb][1]} and {_BY_NAME[also][1]}" if pair else _BY_NAME[verb][1]
        clauses.append(f"{said} {_and_list(subjects)}")
    clauses = list(dict.fromkeys(clauses))

    for how_many in range(min(most, len(clauses)), 0, -1):
        kept = clauses[:how_many]
        sentence = _capitalised(_joined(kept))
        if len(sentence) <= limit:
            return sentence
    return ""


def _joined(clauses: list[str]) -> str:
    if len(clauses) == 1:
        return f"{clauses[0]}."
    # A clause that already contains "and" needs the comma before the last
    # one, or the sentence runs two "and"s together.
    separator = ", and " if len(clauses) > 2 and any(" and " in c for c in clauses) else " and "
    return f"{', '.join(clauses[:-1])}{separator}{clauses[-1]}."


def _and_list(words: list[str]) -> str:
    if len(words) <= 1:
        return words[0] if words else ""
    return f"{', '.join(words[:-1])} and {words[-1]}"
