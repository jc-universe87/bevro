"""The provider-neutral shape of a routing decision.

This is what every router returns and what a routing model must produce as
structured output. It is a proposal: `app.routing.validate` decides whether
Bevro will act on it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RoutingSource(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    FALLBACK = "fallback"  # the LLM route failed or was rejected; deterministic rules decided
    EXPLICIT = "explicit"  # the person chose the provider themselves


class InputRequestSpec(BaseModel):
    """Something the router needs from the person before work can start.

    kind "workspace": which approved project to work in (Bevro supplies the options).
    kind "question":  one short free-text question, e.g. "Which city?".
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["workspace", "question"]
    prompt: str = Field(default="", max_length=200)


class PlanStep(BaseModel):
    """A step in a plan. `provider_id` is optional so that a later router can
    describe multi-provider work; V1 executes one provider."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(max_length=200)
    provider_id: str | None = Field(default=None, max_length=80)


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Provider ids (slugs) from the supplied catalogue. Empty = nothing suitable.
    selected_provider_ids: list[str] = Field(default_factory=list, max_length=8)
    needs_input: bool = False
    input_request: InputRequestSpec | None = None
    # Short internal notes, never shown to the person as-is.
    rationale: str | None = Field(default=None, max_length=400)
    plan: list[PlanStep] = Field(default_factory=list, max_length=12)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    routing_source: RoutingSource = RoutingSource.DETERMINISTIC
    # Why there is no selection, when there is none. Internal.
    reason: str | None = Field(default=None, max_length=300)

    @property
    def provider_id(self) -> str | None:
        return self.selected_provider_ids[0] if self.selected_provider_ids else None

    def to_metadata(self, *, router_version: str, backend: str | None = None, fallback_reason: str | None = None) -> dict:
        """What Bevro keeps on the task. No prompts, no raw responses."""
        return {
            "source": self.routing_source.value,
            "router_version": router_version,
            "backend": backend,
            "selected_provider_ids": list(self.selected_provider_ids),
            "confidence": self.confidence,
            "rationale": self.rationale,
            "plan": [step.model_dump() for step in self.plan],
            "fallback_reason": fallback_reason,
        }


# JSON Schema a routing model must satisfy. Derived from the model so the two
# cannot drift; `routing_source` and `reason` are Bevro's, not the model's.
def response_schema() -> dict:
    schema = RoutingDecision.model_json_schema()
    for key in ("routing_source",):
        schema["properties"].pop(key, None)
    return schema
