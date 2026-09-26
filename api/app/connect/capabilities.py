"""Conservative capability inference from whatever text discovery found.

A small vocabulary maps words in a name, description or README to capability
ids the routers already understand. Fewer accurate capabilities beat many
speculative ones: a capability needs a clear word, and there are at most
five. When nothing matches, the draft says so and the person fills it in.
"""

from __future__ import annotations

import re

from app.connect.draft import DraftCapability

MAX_CAPABILITIES = 5
# A word mentioned once in a README body (weight 0.34) is not evidence; three mentions are.
MIN_SCORE = 1.0

# (capability id, title, pattern). Order is preference when scores tie.
VOCABULARY: list[tuple[str, str, re.Pattern[str]]] = [
    ("research", "Research", re.compile(r"\b(research\w*|investigat\w*|intelligence|look(s|ing)? up|find(s|ing)? (out|information))\b", re.I)),
    ("competitor_analysis", "Competitor analysis", re.compile(r"\b(competitor\w*|competiti(ve|on)|rival\w*)\b", re.I)),
    ("market_analysis", "Market analysis", re.compile(r"\b(market(s|ing)?|industry|landscape|trend\w*)\b", re.I)),
    ("product_strategy", "Product strategy", re.compile(r"\b(strateg\w*|roadmap\w*|positioning|recommendation\w*)\b", re.I)),
    ("reporting", "Reports", re.compile(r"\b(report\w*|briefing\w*|dossier|digest\w*)\b", re.I)),
    ("summarise", "Summarise", re.compile(r"\b(summari[sz]\w*|recap\w*|tl;?dr)\b", re.I)),
    # "Compose" on its own is Docker Compose in most READMEs; composing
    # *something* is writing.
    ("draft", "Draft text", re.compile(r"\b(drafts?|drafting|write (text|emails?|posts?|replies|copy)|compos(e|es|ing) (text|emails?|messages?|posts?|replies|letters?)|copywrit\w*)\b", re.I)),
    ("translate", "Translate", re.compile(r"\b(translat\w*)\b", re.I)),
    ("email", "Handle email", re.compile(r"\b(e-?mails?|inbox|mailbox)\b", re.I)),
    ("schedule", "Schedule", re.compile(r"\b(schedul\w*|calendar\w*|booking\w*|appointment\w*)\b", re.I)),
    ("monitor", "Monitor", re.compile(r"\b(monitor\w*|watch\w*|alert\w*|track(s|ing)? changes)\b", re.I)),
    ("search", "Search", re.compile(r"\b(search\w*|retriev\w*|index\w*)\b", re.I)),
    ("knowledge", "Search what you know", re.compile(r"\b(knowledge (base|workspace|management)|personal knowledge|second brain|personal wiki|what you(\u2019|')ve (learned|noted|written))\b", re.I)),
    ("archive", "Filing and finding documents", re.compile(r"\b(archiv\w*|binders?|filing (cabinet|system)|where (it|a document) (lives|is filed))\b", re.I)),
    ("coding", "Coding", re.compile(r"\b(coding|code (changes|review)|refactor\w*|programming|developer tool\w*)\b", re.I)),
    ("data_analysis", "Data analysis", re.compile(r"\b(analy[sz]\w* data|data analysis|analytics|statistic\w*|spreadsheet\w*)\b", re.I)),
    ("events.allocate", "Allocate participants", re.compile(r"\b(allocat\w*|participants?|attendees?|registration\w*)\b", re.I)),
    ("customer_support", "Customer support", re.compile(r"\b(support ticket\w*|helpdesk|customer (support|service))\b", re.I)),
    ("documents", "Documents", re.compile(r"\b(documents?|pdf\w*|files?)\b", re.I)),
    ("notes", "Notes", re.compile(r"\b(notes?|notebook\w*|memo\w*)\b", re.I)),
    ("quotes", "Quotes and orders", re.compile(r"\b(quot(e|es|ation)\w*|invoice\w*|orders?)\b", re.I)),
]

# Words so generic they should not make a capability on their own.
_GENERIC_ONLY = {"documents", "notes", "search", "reporting"}


def infer_capabilities(*texts: str | None, weights: tuple[float, ...] = ()) -> list[DraftCapability]:
    """Score each vocabulary entry across the texts. Earlier texts (name,
    description) may be weighted higher than a README excerpt."""
    scores: dict[str, float] = {}
    for index, text in enumerate(texts):
        if not text:
            continue
        weight = weights[index] if index < len(weights) else 1.0
        for cap_id, _title, pattern in VOCABULARY:
            hits = len(pattern.findall(text))
            if hits:
                scores[cap_id] = scores.get(cap_id, 0.0) + weight * min(hits, 3)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], [c[0] for c in VOCABULARY].index(kv[0])))
    out: list[DraftCapability] = []
    for cap_id, score in ranked:
        if score < MIN_SCORE or (cap_id in _GENERIC_ONLY and score < 2.0):
            continue
        title = next(t for c, t, _ in VOCABULARY if c == cap_id)
        out.append(DraftCapability(id=cap_id, title=title))
        if len(out) >= MAX_CAPABILITIES:
            break
    return out


_ID_RE = re.compile(r"[^a-z0-9]+")


def capability_from_text(text: str) -> DraftCapability | None:
    """A capability typed or edited by the person: "Product strategy" -> product_strategy."""
    title = " ".join((text or "").split())[:80]
    if not title:
        return None
    cap_id = _ID_RE.sub("_", title.lower()).strip("_")[:80]
    if not cap_id:
        return None
    known = next((t for c, t, _ in VOCABULARY if c == cap_id), None)
    return DraftCapability(id=cap_id, title=known or title)


def capabilities_from_summary(summary: str) -> list[DraftCapability]:
    """A comma- or newline-separated summary the person edited on the confirmation page."""
    out: list[DraftCapability] = []
    for part in re.split(r"[,\n;]+", summary or ""):
        cap = capability_from_text(part)
        if cap and all(c.id != cap.id for c in out):
            out.append(cap.model_copy(update={"by": "person"}))
        if len(out) >= 8:
            break
    return out


def capability_from_tool(name: str, description: str | None) -> DraftCapability:
    """MCP tool or API operation -> capability. The id stays close to the tool name."""
    cap_id = _ID_RE.sub("_", name.lower()).strip("_")[:80] or "tool"
    title = " ".join(w[:1].upper() + w[1:] for w in re.split(r"[_\-.\s]+", name) if w)[:80] or name[:80]
    desc = " ".join((description or "").split())[:200] or None
    return DraftCapability(id=cap_id, title=title, description=desc)
