"""Anthropic Messages API backend. Structured output is obtained by forcing a
single tool call whose input schema is the decision schema."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.routing.catalogue import CatalogueEntry
from app.routing.decision import RoutingDecision, response_schema
from app.routing.models import RoutingModelError
from app.routing.models._shared import parse_decision, user_message
from app.routing.prompt import ROUTER_SYSTEM_PROMPT

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
TOOL_NAME = "routing_decision"


class AnthropicRoutingModel:
    name = "anthropic"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.model = settings.router_model or "claude-haiku-4-5-20251001"
        self.base_url = (settings.router_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = settings.router_api_key
        self.timeout = settings.router_timeout_seconds
        self._transport = transport

    def structured_payload(self, system: str, user: str, schema: dict[str, Any], name: str, max_tokens: int = 400) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": name, "description": f"Record the {name.replace('_', ' ')}.", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": name},
        }

    def build_payload(self, request: str, catalogue: list[CatalogueEntry], context: dict[str, Any]) -> dict[str, Any]:
        return self.structured_payload(ROUTER_SYSTEM_PROMPT, user_message(request, catalogue, context), response_schema(), TOOL_NAME)

    def structured(self, system: str, user: str, schema: dict[str, Any], name: str, max_tokens: int = 400) -> Any:
        """One forced tool call: the tool input, parsed but not yet validated."""
        if not self.api_key:
            raise RoutingModelError("no routing API key configured")
        headers = {"Content-Type": "application/json", "x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.post(f"{self.base_url}/messages", json=self.structured_payload(system, user, schema, name, max_tokens), headers=headers)
        except httpx.TimeoutException as exc:
            raise RoutingModelError("routing model timed out") from exc
        except httpx.HTTPError as exc:
            raise RoutingModelError(f"routing model unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RoutingModelError(f"routing model answered HTTP {response.status_code}")
        try:
            blocks = response.json()["content"]
            return next(b["input"] for b in blocks if b.get("type") == "tool_use" and b.get("name") == name)
        except (ValueError, KeyError, StopIteration, TypeError) as exc:
            raise RoutingModelError("routing model did not return a decision") from exc

    def route(self, request: str, catalogue: list[CatalogueEntry], context: dict[str, Any]) -> RoutingDecision:
        return parse_decision(self.structured(ROUTER_SYSTEM_PROMPT, user_message(request, catalogue, context), response_schema(), TOOL_NAME))
