"""Turns "What should this agent do?" into a declared provider.

V1 derives a name, capabilities and a permissions summary from the wording
with plain rules - no model call. Activation stores a *declared* provider:
a record with a specification but no way to run yet. A builder provider
(Claude Code, Codex, ...) can later pick up `adapter.spec` and attach a real
adapter reference without touching the task model.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.models import Provider
from app.services.providers import register_provider

# Verb -> (capability id, title, permission it implies)
_VERBS: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"\b(summari[sz]e|digest|recap)\b", re.I), "summarise", "Summarise", "Read the content it is given"),
    (re.compile(r"\b(research|find|look up|compare|analy[sz]e)\b", re.I), "research", "Research", "Search public sources"),
    (re.compile(r"\b(draft|write|compose|reply)\b", re.I), "draft", "Draft text", "Produce text for you to review"),
    (re.compile(r"\b(schedule|book|calendar|remind)\b", re.I), "schedule", "Schedule", "Read and propose calendar entries"),
    (re.compile(r"\b(email|inbox|mail)\b", re.I), "email", "Handle email", "Read your inbox"),
    (re.compile(r"\b(monitor|watch|track|alert)\b", re.I), "monitor", "Monitor", "Check sources on a schedule"),
    (re.compile(r"\b(allocat\w*|organi[sz]e|assign)\b", re.I), "organise", "Organise", "Change records in connected apps"),
    (re.compile(r"\b(translate)\b", re.I), "translate", "Translate", "Read the content it is given"),
]

_STOP = {
    "a", "an", "the", "that", "which", "who", "to", "for", "my", "me", "and", "of", "with", "in", "on", "at", "by",
    "agent", "assistant", "i", "want", "should", "can", "please", "each", "every", "all", "any", "anything",
    "morning", "evening", "daily", "weekly", "when", "then", "it", "them", "this", "so", "as", "from", "into",
}


def _name_from(description: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", description) if w.lower() not in _STOP]
    picked = words[:2] if words else ["New", "agent"]
    name = " ".join(w.capitalize() for w in picked)
    return name[:60]


def preview_spec(description: str):
    """The agreed description of an agent, from one sentence."""
    from app.create.specmodel import get_spec_model

    return get_spec_model().spec_for(description)


def preview(description: str) -> dict[str, Any]:
    text = " ".join(description.split())
    capabilities: list[dict[str, Any]] = []
    permissions: list[str] = []
    for pattern, cap_id, title, permission in _VERBS:
        if pattern.search(text) and all(c["id"] != cap_id for c in capabilities):
            capabilities.append({"id": cap_id, "title": title})
            if permission not in permissions:
                permissions.append(permission)
    if not capabilities:
        capabilities.append({"id": "general", "title": "General help"})
        permissions.append("Read the content it is given")
    permissions.append("Ask you before anything irreversible")
    return {
        "name": _name_from(text),
        "description": text[:200],
        "capabilities": capabilities,
        "permissions": permissions,
        "enabled": True,
    }


def activate(db: Session, spec: dict[str, Any], source_description: str) -> Provider:
    provider = register_provider(
        db,
        {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "enabled": bool(spec.get("enabled", True)),
            "capabilities": spec.get("capabilities", []),
            "adapter": {
                "kind": "declared",
                "spec": {"source_description": source_description, "permissions": spec.get("permissions", [])},
            },
            "icon": {"kind": "letter", "text": spec["name"][:1].upper()},
            "origin": "created",
        },
    )
    db.commit()
    db.refresh(provider)
    return provider
