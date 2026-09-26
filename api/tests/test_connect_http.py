"""URL discovery through a fake service, and the HTTP adapter's request profile."""

import json

import httpx
import pytest

from adapters import InvocationRequest, ProviderSpec, get_adapter
from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed
from app.connect.strategies.http import HttpDiscoveryStrategy
from app.connect.targets import classify_target

OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "Sales Desk", "description": "Quotes and follow-ups for the sales team.", "version": "2.0"},
    "paths": {
        "/ask": {"post": {"summary": "Ask the sales assistant", "operationId": "ask", "tags": ["quotes"], "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Ask"}}}}, "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {"answer": {"type": "string"}}}}}}}}},
        "/customers/{id}": {"get": {"summary": "Customer", "tags": ["customers"]}},
        "/quotes": {"post": {"summary": "Create a quote", "tags": ["quotes"], "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"customer_id": {"type": "string"}, "amount": {"type": "number"}}}}}}}},
    },
    "components": {"schemas": {"Ask": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
}


def fake_service(*, openapi=None, manifest=None, health=True, secured=False, mcp=False):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.method in ("GET", "POST")
        if request.method == "POST" and request.url.path == "/ask":
            body = json.loads(request.content)
            return httpx.Response(200, json={"answer": f"Quote for: {body['query']}", "link": "https://sales.example/q/1"})
        if request.method == "POST" and mcp and request.url.path == "/mcp":
            msg = json.loads(request.content)
            if msg.get("id") is None:
                return httpx.Response(202)
            if msg["method"] == "initialize":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "notes-mcp", "version": "3"}, "instructions": "Search and summarise notes"}}, headers={"Mcp-Session-Id": "s1"})
            if msg["method"] == "tools/list":
                return httpx.Response(200, content="event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "search_notes", "description": "Search notes", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]}}) + "\n\n", headers={"Content-Type": "text/event-stream"})
            if msg["method"] == "tools/call":
                assert request.headers.get("mcp-session-id") == "s1"
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "Found 2 notes about " + msg["params"]["arguments"]["query"]}]}})
        if request.method != "GET":
            return httpx.Response(405)
        if request.url.path == "/.well-known/bevro.json" and manifest:
            return httpx.Response(200, json=manifest)
        if request.url.path == "/openapi.json" and openapi:
            spec = dict(openapi)
            if secured:
                spec = {**spec, "security": [{"bearer": []}], "components": {**spec.get("components", {}), "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}}
            return httpx.Response(200, json=spec)
        if request.url.path == "/health" and health:
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/":
            return httpx.Response(200, text="<html><title>Bare Service</title></html>", headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


def discover(url: str, transport) -> tuple:
    service = ConnectionDiscoveryService([HttpDiscoveryStrategy(transport)], use_assist=False)
    return service.discover(classify_target(url), DiscoveryContext())


def test_openapi_discovery_infers_operation_auth_and_capabilities():
    transport, calls = fake_service(openapi=OPENAPI)
    draft = discover("http://sales.local", transport)
    assert draft.name == "Sales Desk" and draft.mechanism == "http" and draft.invocable
    assert draft.confidence == "high"
    assert draft.adapter["config"]["invoke"] == {"method": "POST", "path": "/ask", "body": {"query": "{request}"}}
    assert draft.adapter["config"]["response"] == {"text": "answer", "summary": "answer"}
    assert draft.adapter["config"]["health_path"] == "/health"
    assert {c.id for c in draft.capabilities} == {"quotes", "customers"}
    assert not draft.auth.required
    assert all(c.startswith("GET") for c in calls)  # discovery never calls anything that could change state


def test_openapi_with_bearer_security_reveals_authentication():
    transport, _ = fake_service(openapi=OPENAPI, secured=True)
    draft = discover("http://sales.local", transport)
    assert draft.auth.required and draft.auth.secret_name == "api_key"
    assert draft.adapter["config"]["auth"] == {"type": "bearer", "secret": "api_key"}


def test_bevro_manifest_is_an_optional_fast_path():
    manifest = {"name": "Calendar Demo", "description": "Calendar", "capabilities": [{"id": "calendar.schedule", "title": "Plan the week"}], "invoke_path": "/bevro/invoke", "auth": {"type": "bearer"}, "app_url": "https://calendar.example/app"}
    transport, calls = fake_service(openapi=OPENAPI, manifest=manifest)
    draft = discover("http://calendar-demo.local", transport)
    assert draft.name == "Calendar Demo" and draft.confidence == "high"
    assert draft.adapter["config"]["invoke_path"] == "/bevro/invoke" and draft.app_url == "https://calendar.example/app"
    assert draft.auth.required
    assert "GET /openapi.json" not in calls


def test_service_that_describes_nothing_is_low_confidence_and_not_invocable():
    transport, _ = fake_service()
    draft = discover("http://bare.local", transport)
    assert draft.name == "Bare Service" and draft.confidence == "low" and not draft.invocable
    assert draft.public()["note"] == "Bevro found this, but couldn't tell what it's for."


def test_unreachable_address_fails_plainly():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(DiscoveryFailed) as exc:
        discover("http://nothing.local", httpx.MockTransport(down))
    assert "Nothing answered" in str(exc.value)


def test_mcp_over_http_lists_tools_and_picks_the_asking_tool():
    transport, _ = fake_service(mcp=True)
    draft = discover("http://notes.local/mcp", transport)
    assert draft.mechanism == "mcp" and draft.confidence == "high" and draft.invocable
    assert draft.name == "Notes Mcp" and draft.description == "Search and summarise notes"
    assert [c.id for c in draft.capabilities] == ["search_notes"]
    assert draft.adapter["config"]["tool"] == {"name": "search_notes", "argument": "query"}
    assert get_adapter("mcp").execution_for(draft.adapter) == "inline"


def test_http_adapter_uses_the_discovered_profile(monkeypatch):
    transport, calls = fake_service(openapi=OPENAPI)
    real_request = httpx.request

    def patched(method, url, **kw):
        with httpx.Client(transport=transport) as client:
            return client.request(method, url, **{k: v for k, v in kw.items() if k in ("json", "headers", "params")})

    monkeypatch.setattr(httpx, "request", patched)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: patched("GET", url, **kw))
    spec = ProviderSpec(id="1", slug="sales", name="Sales Desk", adapter={"kind": "http", "config": {"base_url": "http://sales.local", "invoke": {"method": "POST", "path": "/ask", "body": {"query": "{request}"}}, "response": {"text": "answer", "summary": "answer", "link": "link"}}})
    result = get_adapter("http").invoke(spec, InvocationRequest(task_id="t", run_id="r", request="Quote for 12 chairs"))
    assert result.state == "completed" and result.summary == "Quote for: Quote for 12 chairs"
    assert [a.type for a in result.artifacts] == ["report", "deep_link"]
    assert result.artifacts[1].external_url == "https://sales.example/q/1"
    assert "POST /ask" in calls
    assert httpx.request is not real_request
