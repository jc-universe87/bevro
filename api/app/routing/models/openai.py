"""OpenAI-compatible chat completions backend (OpenAI itself, or any server
that speaks the same API: vLLM, Ollama, LiteLLM, ...). Uses strict JSON-schema
structured output; the response is validated again by Bevro regardless."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.routing.catalogue import CatalogueEntry
from app.routing.decision import RoutingDecision, response_schema
from app.routing.models import RoutingModelError
from app.routing.models._shared import parse_decision, user_message
from app.routing.prompt import ROUTER_SYSTEM_PROMPT

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _strict_schema(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """OpenAI's strict mode needs every property required and no extras."""
    schema = schema if schema is not None else response_schema()

    def tighten(node: dict[str, Any]) -> None:
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
            for prop in node["properties"].values():
                tighten(prop)
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []):
                tighten(sub)
        if "items" in node:
            tighten(node["items"])
        if "$defs" in node:
            for sub in node["$defs"].values():
                tighten(sub)

    tighten(schema)
    return schema


class OpenAIRoutingModel:
    name = "openai"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.model = settings.router_model or "gpt-4o-mini"
        self.base_url = (settings.router_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = settings.router_api_key
        self.timeout = settings.router_timeout_seconds
        self._transport = transport

    def structured_payload(self, system: str, user: str, schema: dict[str, Any], name: str, max_tokens: int = 400) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": _strict_schema(schema)},
            },
        }

    def build_payload(self, request: str, catalogue: list[CatalogueEntry], context: dict[str, Any]) -> dict[str, Any]:
        return self.structured_payload(ROUTER_SYSTEM_PROMPT, user_message(request, catalogue, context), response_schema(), "routing_decision")

    def structured(self, system: str, user: str, schema: dict[str, Any], name: str, max_tokens: int = 400) -> Any:
        """One structured completion: the model's JSON, parsed but not yet validated."""
        if not self.api_key and "api.openai.com" in self.base_url:
            raise RoutingModelError("no routing API key configured")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=self.structured_payload(system, user, schema, name, max_tokens), headers=headers)
        except httpx.TimeoutException as exc:
            raise RoutingModelError("routing model timed out") from exc
        except httpx.HTTPError as exc:
            raise RoutingModelError(f"routing model unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RoutingModelError(f"routing model answered HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RoutingModelError("routing model response had no content") from exc
        return content

    def route(self, request: str, catalogue: list[CatalogueEntry], context: dict[str, Any]) -> RoutingDecision:
        return parse_decision(self.structured(ROUTER_SYSTEM_PROMPT, user_message(request, catalogue, context), response_schema(), "routing_decision"))
