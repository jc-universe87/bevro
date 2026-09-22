"""SpecModel: one sentence in, an AgentSpec out. Nothing else.

Provider-neutral, like the router's model interface: a small structured
completion through whichever backend is configured, and plain local rules when
none is. It never writes code and never sees a filesystem - that is the
builder's job, kept deliberately separate.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from pydantic import ValidationError

from app.config import get_settings
from app.create.spec import AgentSpec, Permission, capability

log = logging.getLogger("bevro.create")

SPEC_PROMPT_VERSION = "1"
SPEC_SYSTEM_PROMPT = """You turn a person's sentence about an agent they want into a structured description for Bevro, a workspace that hands work to agents.

You never write code and never design an implementation. You only describe what the agent is for.

Rules:
- name: two or three plain words, no vendor names, no "AI" or "bot".
- description: one sentence a non-technical person would recognise.
- purpose: one sentence on what decision or work it serves.
- capabilities: 1 to 4, each a short title with a lowercase id joined by underscores. Only what the sentence clearly implies. Prefer these ids when they fit: research, competitor_analysis, market_analysis, product_strategy, reporting, summarise, draft, translate, email, schedule, monitor, search, coding, code_review, data_analysis, categorise.
- permissions: only from web, files_read, files_write, run_commands, external_api, schedule. Ask for the least that would work.
- integrations: named outside services the work clearly needs (for example "github"), or empty.
- schedule: plain words only if the person asked for recurring work ("every Monday"), otherwise null.
- output_expectation: one of report, text, structured, file, summary.
- complexity: small unless the work clearly needs more.

Return only the JSON object."""


class SpecModelError(Exception):
    """The model could not produce a usable spec. The caller falls back."""


class SpecModel(Protocol):
    name: str

    def spec_for(self, description: str) -> AgentSpec: ...


# --------------------------------------------------------------------------- rules

# Verb -> capability, and what it implies the agent will need.
_RULES: list[tuple[re.Pattern[str], str, list[Permission]]] = [
    (re.compile(r"\b(research\w*|investigat\w*|find out|look up)\b", re.I), "Research", [Permission.WEB]),
    (re.compile(r"\b(competitor\w*|rival\w*|competiti(ve|on))\b", re.I), "Competitor analysis", [Permission.WEB]),
    (re.compile(r"\b(market\w*|industry|landscape|trend\w*)\b", re.I), "Market analysis", [Permission.WEB]),
    (re.compile(r"\b(strateg\w*|roadmap\w*|positioning)\b", re.I), "Product strategy", []),
    (re.compile(r"\b(report\w*|briefing\w*|digest\w*)\b", re.I), "Reports", [Permission.FILES_WRITE]),
    (re.compile(r"\b(summari[sz]\w*|recap\w*)\b", re.I), "Summarise", []),
    (re.compile(r"\b(review\w*|pull request\w*|\bPRs?\b|diff\w*|code)\b", re.I), "Code review", [Permission.FILES_READ]),
    (re.compile(r"\b(categoris\w*|categoriz\w*|classif\w*|sort\w*|tag\w*)\b", re.I), "Categorise", []),
    (re.compile(r"\b(drafts?|draft\w*|writ\w*|compos\w*)\b", re.I), "Draft text", []),
    (re.compile(r"\b(translat\w*)\b", re.I), "Translate", []),
    (re.compile(r"\b(monitor\w*|watch\w*|track\w*|alert\w*)\b", re.I), "Monitor", [Permission.WEB]),
    (re.compile(r"\b(e-?mails?|inbox)\b", re.I), "Handle email", [Permission.EXTERNAL_API]),
    (re.compile(r"\b(paper\w*|article\w*|publication\w*)\b", re.I), "Reading", [Permission.WEB]),
]
_SCHEDULE = re.compile(r"\b(every (day|morning|week|monday|tuesday|wednesday|thursday|friday|month|quarter)|daily|weekly|monthly|quarterly|each (day|morning|week|month))\b", re.I)
_OUTPUT = [
    (re.compile(r"\breports?\b|\bbriefing\b", re.I), "report"),
    (re.compile(r"\b(categoris\w*|categoriz\w*|classif\w*|structured|table|list of)\b", re.I), "structured"),
    (re.compile(r"\bsummar(y|ies|ise|ize)\b", re.I), "summary"),
    (re.compile(r"\bfiles?\b|\bpdf\b|\bspreadsheet\b", re.I), "file"),
]
_STOP = {
    "a", "an", "the", "that", "which", "who", "to", "for", "my", "me", "and", "of", "with", "in", "on", "at", "by",
    "agent", "assistant", "i", "want", "should", "can", "please", "each", "every", "all", "any", "anything", "about",
    "morning", "evening", "daily", "weekly", "when", "then", "it", "them", "this", "so", "as", "from", "into", "give",
    "produce", "make", "short", "new", "follow", "supplied", "identify", "point", "out",
}


def _name_from(description: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", description) if w.lower() not in _STOP]
    picked = words[:3] if len(words) >= 3 else words[:2] or ["New", "Agent"]
    return " ".join(w.capitalize() for w in picked)[:60]


class RulesSpecModel:
    """The offline default: local rules, no call to anything."""

    name = "rules"

    def spec_for(self, description: str) -> AgentSpec:
        text = " ".join(description.split())
        capabilities = []
        permissions: list[Permission] = []
        for pattern, title, implied in _RULES:
            if pattern.search(text) and all(c.title != title for c in capabilities):
                capabilities.append(capability(title))
                permissions += [p for p in implied if p not in permissions]
            if len(capabilities) >= 4:
                break
        if not capabilities:
            capabilities.append(capability("General help"))
        schedule_match = _SCHEDULE.search(text)
        if schedule_match and Permission.SCHEDULE not in permissions:
            permissions.append(Permission.SCHEDULE)
        output = next((name for pattern, name in _OUTPUT if pattern.search(text)), "text")
        if output in ("report", "file") and Permission.FILES_WRITE not in permissions:
            permissions.append(Permission.FILES_WRITE)
        return AgentSpec(
            name=_name_from(text),
            description=text[:300],
            purpose=text[:400],
            capabilities=capabilities,
            output_expectation=output,
            permissions=permissions,
            schedule=" ".join(schedule_match.group(0).split()) if schedule_match else None,
            source="rules",
        )


# --------------------------------------------------------------------------- model

class LLMSpecModel:
    """One structured completion through the configured routing backend.

    The same key and backend the router uses; a different prompt and schema.
    It receives the person's sentence and nothing else.
    """

    name = "llm"

    def __init__(self, model: Any, fallback: SpecModel | None = None) -> None:
        self.model = model
        self.fallback = fallback or RulesSpecModel()

    def spec_for(self, description: str) -> AgentSpec:
        structured = getattr(self.model, "structured", None)
        if not callable(structured):
            return self.fallback.spec_for(description)
        schema = AgentSpec.model_json_schema()
        for key in ("source", "version"):
            schema["properties"].pop(key, None)
        try:
            raw = structured(SPEC_SYSTEM_PROMPT, json.dumps({"request": " ".join(description.split())[:2000]}), schema, "agent_spec", 700)
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, dict):
                raise SpecModelError("the model returned an unexpected shape")
            raw.pop("source", None)
            spec = AgentSpec.model_validate({**raw, "source": "model"})
        except (ValidationError, ValueError, TypeError) as exc:
            log.warning("spec model output was unusable (%s); using local rules", exc)
            return self.fallback.spec_for(description)
        except Exception as exc:  # noqa: BLE001 - never block Create on a model
            log.warning("spec model unavailable (%s); using local rules", exc)
            return self.fallback.spec_for(description)
        if not spec.capabilities:
            spec.capabilities = self.fallback.spec_for(description).capabilities
        if not spec.description.strip():
            spec.description = " ".join(description.split())[:300]
        return spec


_model: SpecModel | None = None


def build_spec_model() -> SpecModel:
    settings = get_settings()
    if settings.router_mode.lower() != "llm":
        return RulesSpecModel()
    try:
        from app.routing.models import RoutingModelError, get_routing_model

        return LLMSpecModel(get_routing_model(settings))
    except Exception as exc:  # noqa: BLE001
        log.info("no model available for Create (%s); using local rules", exc)
        return RulesSpecModel()


def get_spec_model() -> SpecModel:
    global _model
    if _model is None:
        _model = build_spec_model()
    return _model


def set_spec_model(model: SpecModel | None) -> None:
    """Swap the model (tests) or reset to configuration (None)."""
    global _model
    _model = model
