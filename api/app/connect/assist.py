"""Optional model assistance for discovery: name, description and capabilities.

Reuses the routing model (same backend, same key) and is off unless routing
is in `llm` mode and BEVRO_DISCOVERY_ASSIST is not "off". It only ever sees
the small sanitised summary built by `evidence_for()`: package metadata, a
README excerpt, entry-point labels, dependency and script names, tool or
operation descriptions. Never source files, never .env, never paths.

Discovery works without it; a failure here leaves the deterministic draft as
it was.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.connect.draft import DraftCapability, ProviderDraft

log = logging.getLogger("bevro.connect.assist")

ASSIST_PROMPT_VERSION = "1"
ASSIST_SYSTEM_PROMPT = """You are helping Bevro, a workspace that hands requests to agents, describe an existing agent or service that a person wants to connect.

You receive a short, sanitised summary of what Bevro could read: metadata, a README excerpt, entry points, dependency names, tool or operation descriptions.

Return:
- name: a short human name for the provider (keep the given name unless it is clearly a package slug).
- description: one plain sentence (max 200 characters) saying what it does for a person.
- capabilities: 1 to 5 capabilities it clearly offers. Each has an id (lowercase, words joined by underscores), a short title and an optional one-line description. Be conservative: only what the evidence supports. Prefer these ids when they fit: research, competitor_analysis, market_analysis, product_strategy, reporting, summarise, draft, translate, email, schedule, monitor, search, coding, data_analysis.
- primary_runtime_id: when "runtimes" lists more than one way this installation can run, the id of the one that looks like the intended, primary way to give it work (an already running service beats a script; a documented command beats a guessed one). Otherwise null.
- expected_output: one short phrase saying what it produces (e.g. "a Markdown report file", "a JSON answer"), or null.

Never invent features. Do not mention Bevro. Return only the JSON object."""

MAX_EVIDENCE_CHARS = 4000


class AssistResult(BaseModel):
    name: str = Field(max_length=120)
    description: str = Field(max_length=300)
    capabilities: list[DraftCapability] = Field(max_length=5)
    primary_runtime_id: str | None = Field(default=None, max_length=40)
    expected_output: str | None = Field(default=None, max_length=120)


def enabled() -> bool:
    settings = get_settings()
    return settings.router_mode.lower() == "llm" and settings.discovery_assist.lower() != "off"


def evidence_for(draft: ProviderDraft, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Exactly what the model may see. Built from the draft's public view plus
    a few named, bounded extras (README excerpt, dependency names...)."""
    public = draft.public()
    payload: dict[str, Any] = {
        "name": public["name"],
        "description": public["description"][:600],
        "kind": public["mechanism_label"],
        "entry_point": public["invocation_label"],
        "evidence": public["evidence"][:12],
        "capabilities_so_far": [c["title"] for c in public["capabilities"]],
    }
    for key in ("readme_excerpt", "dependencies", "scripts", "tools", "operations", "runtimes"):
        value = (extra or {}).get(key)
        if value:
            payload[key] = value
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > MAX_EVIDENCE_CHARS:
        payload["readme_excerpt"] = str(payload.get("readme_excerpt", ""))[:800]
        payload["dependencies"] = list(payload.get("dependencies") or [])[:20]
        payload["tools"] = list(payload.get("tools") or [])[:10]
        payload["operations"] = list(payload.get("operations") or [])[:10]
    return payload


def refine(draft: ProviderDraft, extra: dict[str, Any] | None = None) -> ProviderDraft:
    """Ask the routing model to improve name, description and capabilities. Best effort."""
    if not enabled():
        return draft
    from app.routing.models import RoutingModelError, get_routing_model

    try:
        model = get_routing_model()
    except RoutingModelError as exc:
        log.info("discovery assist unavailable: %s", exc)
        return draft
    structured = getattr(model, "structured", None)
    if not callable(structured):
        return draft
    payload = evidence_for(draft, extra)
    try:
        raw = structured(ASSIST_SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), AssistResult.model_json_schema(), "provider_description", 500)
        if isinstance(raw, str):
            raw = json.loads(raw)
        result = AssistResult.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - assistance is optional; a model problem never blocks Connect
        log.warning("discovery assist failed; keeping the deterministic draft: %s", exc)
        return draft
    update: dict[str, Any] = {"assisted": True, "evidence": [*draft.evidence, "Description refined by the routing model"]}
    if result.capabilities:
        update["capabilities"] = result.capabilities[:5]
    if result.description.strip():
        update["description"] = " ".join(result.description.split())[:300]
    if result.name.strip() and len(result.name) <= 60:
        update["name"] = result.name.strip()
    refined = draft.model_copy(update=update)
    if refined.confidence == "low" and refined.capabilities and refined.invocable:
        refined.confidence = "medium"
    # The model may settle an uncertain runtime choice, but only among what discovery found.
    if refined.choice_needed and result.primary_runtime_id and any(rt.id == result.primary_runtime_id and rt.invocable for rt in refined.runtimes):
        refined.with_runtimes(refined.runtimes, result.primary_runtime_id, False)
        refined.evidence = [*refined.evidence, "Runtime chosen with the routing model's help"]
    if result.expected_output:
        refined.evidence = [*refined.evidence, f"Expected output: {result.expected_output[:120]}"]
    return refined
