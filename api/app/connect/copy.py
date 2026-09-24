"""What a person is told about a provider, as opposed to what Bevro read.

A service's own description is written for whoever integrates with it:
"Thin control API over the X modules. GET routes read. POST routes are
explicit operations." That is *evidence* - it tells Bevro what it found -
and it is the wrong thing to put in front of someone who only wants to know
whether this can help them with anything.

So the two are kept apart. What the service said about itself is stored as
`source_description` and shown under Advanced details, where a technical
fact is what is wanted. What the person reads is built here, in three
layers, from facts rather than from prose:

    summary       one short sentence, on the card
    details       what it does, how it connects
    advanced      the technical account, for when something is wrong

Nothing here knows what any particular service is. It works from the name,
the capabilities the service declared, and the mechanism Bevro reaches it
through - so a careers tool, an issue tracker and a finance system are all
described by the same code.
"""

from __future__ import annotations

import re
from typing import Any

# A card is read at a glance. Anything longer is a paragraph pretending.
MAX_SUMMARY = 140
MAX_STORED = 200
# When Bevro knows nothing at all. Better than showing the technical text.
NOTHING_KNOWN = "Connected service"

# Words that belong to how a thing is built, not to what it can do for
# anyone. Their presence is how Bevro recognises an integration document
# that has wandered into the part of the screen meant for people.
_TECHNICAL = re.compile(
    r"\b(openapi|swagger|endpoints?|api routes?|schemas?|descriptors?|payloads?|"
    r"json|yaml|sdk|middleware|serialis|serializ|webhooks?|stdout|stderr|"
    r"invocation|adapter|runtime|http|https|rest api|crud)\b|"
    r"\b(GET|POST|PUT|PATCH|DELETE)\b|/[a-z0-9_]+/|`",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[.!?](?:\s|$)")

# Capability names that describe the machinery rather than the work. Dropped
# from a summary when enough real ones remain - never when they are all
# there is, because a true plain answer beats an empty one.
_PLUMBING = frozenset(
    {
        "default", "general", "misc", "other", "health", "status", "system", "internal", "debug",
        "meta", "admin", "config", "configuration", "settings", "auth", "authentication",
        "session", "sessions", "token", "tokens", "artifacts", "workspace", "workspaces",
        "profile", "profiles", "account", "accounts", "user", "users",
    }
)

# Adapter kinds grouped by what they *are* to a person. A mechanism, never a
# provider: "command" and "claude_code" are both a program on this machine,
# and neither entry knows which program.
SHAPE_OF = {
    "openapi": "features",
    "http": "prompt",
    "mcp": "tools",
    "mcp_stdio": "tools",
    "command": "program",
    "claude_code": "program",
    "local": "builtin",
    "declared": "unbuilt",
}

# What kind of thing this is, said in the way a person would say it.
WHAT_IT_IS = {
    "features": "{name} is a service you already run, with features of its own.",
    "prompt": "{name} is a service you already run, which answers requests written in plain language.",
    "tools": "{name} is a set of tools you already run.",
    "program": "{name} is a program on this machine.",
    "builtin": "{name} comes with Bevro.",
    "unbuilt": "{name} has been described to Bevro but not built yet.",
}

# How Bevro reaches it, and what that means for whose data is whose.
HOW_IT_CONNECTS = {
    "features": (
        "Bevro connects directly to the service and uses its existing features. "
        "The service keeps its own data; Bevro sends it work and brings the results back here."
    ),
    "prompt": (
        "Bevro connects directly to the service and asks in plain language. "
        "The service keeps its own data; Bevro brings the answer back here."
    ),
    "tools": (
        "Bevro connects directly to the service and uses the tools it offers. "
        "The service keeps its own data; Bevro brings the results back here."
    ),
    "program": (
        "Bevro runs it on this machine and brings the results back here. "
        "It works in the folder it is given."
    ),
    "builtin": "It comes with Bevro and runs wherever Bevro runs.",
    "unbuilt": "There is nothing to connect to yet: Bevro has a description of it, but it has not been built.",
}
ONLY_FROM_HERE = "Bevro reaches it through the Bevro worker running on this machine."
BEVRO_KEEPS_CREDENTIAL = "Bevro holds the credential it needs, stored encrypted."


# A name that ends by saying what kind of interface it is is telling Bevro
# something, not the person. "Career Agent control API" is Career Agent; so
# is "Inventory REST API" Inventory. "Acme Search Service" keeps its Service,
# because that is a word about the thing rather than about its wiring.
_INTERFACE_SUFFIX = re.compile(
    r"\s*[-–|:]?\s*\b(?:(?:control|service|management|admin|public|internal|external|"
    r"rest|restful|http|web|json|graph|graphql|open|core|backend|gateway|gw|gw2)\s+)?"
    r"(?:api|apis|openapi)\b\s*(?:v?\d+(?:\.\d+)*)?\s*$",
    re.IGNORECASE,
)


def display_name(name: str) -> str:
    """The thing's name, without the part that describes its plumbing.

    Only a trailing interface word is dropped, and only when something is
    left: a service actually called "API" keeps the only name it has.
    """
    trimmed = _INTERFACE_SUFFIX.sub("", " ".join(str(name or "").split())).strip(" -–|:")
    return trimmed if len(trimmed) >= 2 else " ".join(str(name or "").split())


# --------------------------------------------------------------------------- is this fit to read?

def is_fit_to_show(text: str | None) -> bool:
    """Would a person recognise this as an answer to "what is this for"?

    Length, sentence count and vocabulary, in that order. A description that
    fails any of them is a document about an interface, however true it is.
    """
    if not text or not text.strip():
        return False
    clean = " ".join(text.split())
    if len(clean) > MAX_STORED:
        return False
    if len(_SENTENCE.findall(clean)) > 2:
        return False
    return not _TECHNICAL.search(clean)


# --------------------------------------------------------------------------- the words

def _titles(capabilities: list[dict[str, Any]] | None) -> list[str]:
    words: list[str] = []
    for capability in capabilities or []:
        if not isinstance(capability, dict):
            continue
        title = str(capability.get("title") or capability.get("id") or "").replace("_", " ").strip()
        if not title:
            continue
        # Lower-cased inside a sentence, unless the service shouts it (an
        # acronym is a name, and renaming someone's thing is not Bevro's job).
        word = title if (title.isupper() and len(title) <= 5) else title[0].lower() + title[1:]
        if word not in words:
            words.append(word)
    return words


def capability_words(capabilities: list[dict[str, Any]] | None) -> list[str]:
    """What this can help with, in the service's own words, worth-first."""
    words = _titles(capabilities)
    real = [w for w in words if w.lower() not in _PLUMBING]
    return real if len(real) >= 2 else words


# Capability titles are written either way: "Documents" or "Write code". A
# sentence has to choose, so the majority decides which frame is used.
_DOING = frozenset(
    """write fix change build find run send make track review search read create update manage answer
    plan check translate draft summarise summarize analyse analyze monitor schedule test deploy edit
    generate compare convert prepare collect import export publish approve allocate""".split()
)


def _is_doing(word: str) -> bool:
    return word.split(" ", 1)[0].lower() in _DOING


def and_list(words: list[str]) -> str:
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _clip(sentence: str, limit: int = MAX_SUMMARY) -> str:
    clean = " ".join(sentence.split())
    if len(clean) <= limit:
        return clean
    cut = clean[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;")
    return f"{cut}…"


# --------------------------------------------------------------------------- the three layers

def _phrases(capabilities: list[dict[str, Any]] | None, *, floor: float = 1.0) -> list:
    """The things this can do, worth-first, as phrasing understands them.

    Groups named after the machinery are left out of a sentence - they are
    still listed under "Can", where completeness is the point.
    """
    from app.connect.phrasing import KNOWN_VERBS, SUPPORTING, Phrase

    out: list[Phrase] = []
    for capability in capabilities or []:
        if not isinstance(capability, dict):
            continue
        verb, subject = str(capability.get("verb") or ""), str(capability.get("subject") or "")
        if verb not in KNOWN_VERBS or not subject:
            continue
        if subject.split(" ")[0] in SUPPORTING or str(capability.get("id") or "") in SUPPORTING:
            continue
        weight = float(capability.get("weight") or 0.0)
        if weight >= floor:
            also = str(capability.get("also") or "") or None
            out.append(Phrase(verb=verb, subject=subject, weight=weight, also=also if also in KNOWN_VERBS else None))
    out.sort(key=lambda phrase: -phrase.weight)
    return out


def summary_for(name: str, capabilities: list[dict[str, Any]] | None, *, stored: str | None = None) -> str:
    """One sentence for the card.

    Copy someone wrote - Bevro's own built-ins, an agent Bevro was asked to
    build, a name the person typed - is theirs and is kept. Everything else
    is built from what the service says it can do.
    """
    from app.connect.phrasing import sentence_from

    # 1. Something someone wrote, if it reads like it.
    if is_fit_to_show(stored):
        return _clip(str(stored), MAX_STORED)
    # 2. What the operations actually do, said as one sentence.
    said = sentence_from(_phrases(capabilities), limit=MAX_SUMMARY, most=3, per_clause=2)
    if said:
        return said
    # 3. Failing that, the names of the things it works with.
    words = capability_words(capabilities)
    if not words:
        return NOTHING_KNOWN  # 4. and failing even that, the plain truth
    opening = "Can" if _mostly_doing(words) else "Helps with"
    for how_many in (4, 3, 2, 1):
        sentence = f"{opening} {and_list(words[:how_many])}."
        if len(sentence) <= MAX_SUMMARY:
            return sentence
    return _clip(f"{opening} {words[0]}.")


def _mostly_doing(words: list[str]) -> bool:
    return sum(1 for w in words if _is_doing(w)) * 2 > len(words)


def what_it_does(name: str, capabilities: list[dict[str, Any]] | None, *, kind: str, stored: str | None = None) -> str:
    """What this can be asked for - the same answer, with more of it.

    Deliberately not "it is a service you already run": that describes how it
    is built, which is the next section's business and Advanced details'
    after that. This section is about whether it is any use.
    """
    from app.connect.phrasing import sentence_from

    said = sentence_from(_phrases(capabilities), limit=240, most=4, per_clause=3)
    if said:
        return said
    words = capability_words(capabilities)
    if words:
        middle = "Can" if _mostly_doing(words) else "Helps with"
        return f"{middle} {and_list(words[:5])}."
    if is_fit_to_show(stored):
        return str(stored).strip().rstrip(".") + "."
    return (
        f"Bevro could not work out what {name} can help with. "
        "What it says about itself is under Advanced details."
    )


def how_it_connects(*, kind: str, host_only: bool = False, bevro_holds_credential: bool = False) -> str:
    """Where the work happens and whose data stays whose."""
    parts = [HOW_IT_CONNECTS.get(SHAPE_OF.get(kind, ""), "Bevro connects to it and brings the results back here.")]
    if host_only:
        parts.append(ONLY_FROM_HERE)
    if bevro_holds_credential:
        parts.append(BEVRO_KEEPS_CREDENTIAL)
    return " ".join(parts)
