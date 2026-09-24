"""Services of the shapes Bevro has to tell apart.

Each of these is a whole pretend service behind an httpx transport: it serves
its own description and answers its own operations. None of them is modelled
on any particular product - they are the *shapes* that exist:

    A  prompt        one endpoint that takes natural language
    B  operational   create a job, run it, read the report
    C  scoped        an operational service whose UI path names a profile
    D  ambiguous     the same, but several profiles and no clue which
    E  spa           an HTML shell on every path, with the real API at the origin
    F  html only     a website, with nothing machine-readable at all
    G  health only   answers /health and nothing else
    H  read only     an operational service that can only be asked questions
    I  state change  an operational service that can also change things
    J  external      an operational service that can contact someone
    K  mcp           an MCP server reached over HTTP
    L  nothing       an address where no connection is ever made

It also holds the runtime builders the location tests share, so that "which
process can see this" can be set up without a live service anywhere.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

SPA_HTML = "<!doctype html><html><head><title>Example App</title></head><body><div id=root></div></body></html>"


def _op(summary: str, *, tags: list[str] | None = None, body: dict[str, Any] | None = None, returns: dict[str, Any] | None = None, params: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"summary": summary, "tags": tags or []}
    if params:
        out["parameters"] = params
    if body:
        out["requestBody"] = {"content": {"application/json": {"schema": body}}}
    out["responses"] = {"200": {"content": {"application/json": {"schema": returns or {"type": "object"}}}}}
    return out


def _path_param(name: str) -> dict[str, Any]:
    return {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}


# --------------------------------------------------------------------------- the descriptors

PROMPT_API = {
    "openapi": "3.1.0",
    "info": {"title": "Sales Desk", "description": "Answers questions about quotes."},
    "paths": {"/ask": {"post": {"summary": "Ask a question", "operationId": "ask", "tags": ["quotes"], "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}}, "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {"answer": {"type": "string"}}}}}}}}}},
}

JOBS_API: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Batch Service", "description": "Runs batch jobs and reports on them."},
    "paths": {
        "/api/jobs": {
            "get": dict(_op("Jobs, newest first", tags=["jobs"], returns={"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "state": {"type": "string"}}}}), **{"operationId": "list_jobs"}),
            "post": dict(_op("Create a job: nothing is executed yet", tags=["jobs"], body={"type": "object", "properties": {"label": {"type": "string"}}}, returns={"type": "object", "properties": {"id": {"type": "string"}}}), **{"operationId": "create_job"}),
        },
        "/api/jobs/{job_id}/execute": {"post": dict(_op("Execute the pending stages", tags=["jobs"], params=[_path_param("job_id")], returns={"type": "object", "properties": {"state": {"type": "string"}}}), **{"operationId": "execute_job"})},
        "/api/jobs/{job_id}/report": {"get": dict(_op("The committed report", tags=["jobs"], params=[_path_param("job_id")], returns={"type": "string"}), **{"operationId": "job_report"})},
        "/api/stats": {"get": dict(_op("Counts, no predictions", tags=["stats"], returns={"type": "object", "properties": {"done": {"type": "integer"}}}), **{"operationId": "stats"})},
    },
}


def _scoped_api(profiles: list[dict[str, str]]) -> dict[str, Any]:
    """An operational service whose work belongs to one profile at a time."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Casework", "description": "Cases, per profile."},
        "paths": {
            "/api/profiles": {"get": dict(_op("Every profile", tags=["profiles"], returns={"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"profile_id": {"type": "string"}, "display_name": {"type": "string"}}}}}}), **{"operationId": "list_profiles"})},
            "/api/profiles/{profile_id}/cases": {"get": dict(_op("Open cases of a profile", tags=["cases"], params=[_path_param("profile_id")], returns={"type": "array", "items": {"type": "object", "properties": {"case_id": {"type": "string"}, "title": {"type": "string"}}}}), **{"operationId": "list_cases"})},
            "/api/profiles/{profile_id}/cases/{case_id}": {"get": dict(_op("One case", tags=["cases"], params=[_path_param("profile_id"), _path_param("case_id")]), **{"operationId": "get_case"})},
            "/api/profiles/{profile_id}/summary": {"get": dict(_op("Counts for a profile", tags=["cases"], params=[_path_param("profile_id")], returns={"type": "object", "properties": {"open": {"type": "integer"}}}), **{"operationId": "profile_summary"})},
        },
        "x-profiles": profiles,
    }


READ_ONLY_API = {
    "openapi": "3.1.0",
    "info": {"title": "Reference Library", "description": "Look things up."},
    "paths": {
        "/api/documents": {"get": dict(_op("Every document", tags=["documents"], returns={"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}}}}), **{"operationId": "list_documents"})},
        "/api/documents/{doc_id}": {"get": dict(_op("One document", tags=["documents"], params=[_path_param("doc_id")]), **{"operationId": "get_document"})},
        "/api/stats": {"get": dict(_op("How many of each", tags=["stats"], returns={"type": "object", "properties": {"total": {"type": "integer"}}}), **{"operationId": "stats"})},
    },
}

STATE_CHANGE_API = {
    "openapi": "3.1.0",
    "info": {"title": "Case Tracker", "description": "Track and update cases."},
    "paths": {
        "/api/cases": {"get": dict(_op("Every case", tags=["cases"], returns={"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}}}}), **{"operationId": "list_cases"})},
        "/api/cases/{case_id}/decision": {"post": dict(_op("Record a decision (append-only; nothing is sent)", tags=["cases"], params=[_path_param("case_id")], body={"type": "object", "properties": {"decision": {"type": "string", "enum": ["yes", "no"]}}, "required": ["decision"]}), **{"operationId": "record_decision"})},
    },
}

EXTERNAL_API = {
    "openapi": "3.1.0",
    "info": {"title": "Outreach", "description": "Cases, and getting in touch."},
    "paths": {
        "/api/contacts": {"get": dict(_op("Every contact", tags=["contacts"], returns={"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}}}}), **{"operationId": "list_contacts"})},
        "/api/contacts/{contact_id}/notify": {"post": dict(_op("Send a message to the contact (external action)", tags=["contacts"], params=[_path_param("contact_id")]), **{"operationId": "notify_contact"})},
    },
}


# --------------------------------------------------------------------------- the services

def service(
    *,
    descriptor: dict[str, Any] | None = None,
    descriptor_path: str = "/openapi.json",
    spa: bool = False,
    health: bool = False,
    profiles: list[dict[str, str]] | None = None,
    data: dict[str, Any] | None = None,
) -> tuple[httpx.MockTransport, list[str]]:
    """One pretend service. Returns its transport and the calls it received.

    `spa=True` answers every unmatched path with an HTML shell and a 200,
    which is what a single-page application really does - and the reason a
    200 cannot be taken as proof that a description was found.
    """
    calls: list[str] = []
    payloads = dict(data or {})

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")

        if descriptor is not None and path == descriptor_path:
            return httpx.Response(200, json=descriptor, headers={"Content-Type": "application/json"})
        if health and path in ("/health", "/api/health"):
            return httpx.Response(200, json={"status": "ok"})
        if profiles is not None and path == "/api/profiles":
            return httpx.Response(200, json={"items": profiles})
        if path in payloads:
            body = payloads[path]
            if isinstance(body, str):
                return httpx.Response(200, content=body, headers={"Content-Type": "text/markdown"})
            return httpx.Response(200, json=body)
        if request.method == "GET" and path in ("/api/jobs", "/api/documents", "/api/cases", "/api/contacts"):
            return httpx.Response(200, json=[{"id": "one"}, {"id": "two"}])
        if request.method == "GET" and path == "/api/stats":
            return httpx.Response(200, json={"done": 2})
        if request.method == "GET" and path.startswith("/api/profiles/") and path.endswith("/cases"):
            return httpx.Response(200, json=[{"case_id": "c1", "title": "First"}])
        if request.method == "GET" and path.startswith("/api/profiles/") and path.endswith("/summary"):
            return httpx.Response(200, json={"open": 1})
        # A created job gets an id; executing or reading one is fine.
        if request.method == "POST" and path == "/api/jobs":
            return httpx.Response(200, json={"id": "job-1"})
        if request.method == "POST" and path.endswith("/execute"):
            return httpx.Response(200, json={"state": "done"})
        if request.method == "GET" and path.endswith("/report"):
            return httpx.Response(200, content="# Report\n\nTwo things happened.\n", headers={"Content-Type": "text/markdown"})
        if spa:
            return httpx.Response(200, content=SPA_HTML, headers={"Content-Type": "text/html; charset=utf-8"})
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler), calls


def html_only() -> tuple[httpx.MockTransport, list[str]]:
    """F: a website. Nothing machine-readable anywhere."""
    return service(spa=True)


def health_only() -> tuple[httpx.MockTransport, list[str]]:
    """G: answers /health and nothing else."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path in ("/health", "/healthz"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler), calls


def scoped_api(profiles: list[dict[str, str]]) -> dict[str, Any]:
    return _scoped_api(profiles)


def as_config(descriptor: dict[str, Any], *, base_url: str = "http://service.local", context: dict[str, str] | None = None) -> dict[str, Any]:
    """The adapter config discovery would have produced for this descriptor."""
    from app.connect.openapi import catalogue_to_json, compile_catalogue

    return {
        "base_url": base_url,
        "descriptor_url": f"{base_url}/openapi.json",
        "operations": catalogue_to_json(compile_catalogue(descriptor)),
        "context": dict(context or {}),
    }


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


# --------------------------------------------------------------------------- K, L

def mcp_service() -> tuple[httpx.MockTransport, list[str]]:
    """K: an MCP server over Streamable HTTP, with one asking tool."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method != "POST" or request.url.path != "/mcp":
            return httpx.Response(404, json={"detail": "not found"})
        msg = json.loads(request.content)
        if msg.get("id") is None:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "Reference Desk", "version": "1"}, "instructions": "Look things up"}},
                headers={"Mcp-Session-Id": "s1"},
            )
        if msg["method"] == "tools/list":
            tools = [{"name": "ask_desk", "description": "Ask the desk", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}})
        if msg["method"] == "tools/call":
            asked = msg["params"]["arguments"]["question"]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": f"The desk says: {asked}"}]}})
        return httpx.Response(400, json={"detail": "unexpected"})

    return httpx.MockTransport(handler), calls


def nothing_answers() -> httpx.MockTransport:
    """L: every connection times out, as a firewalled address does."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route from this process")

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- runtimes

def network_runtime(kind: str = "openapi", *, base: str = "http://service.local", **reach: Any) -> Any:
    """One way in over a network, with who can see it set explicitly."""
    from adapters.runtime import ADAPTER_FOR_KIND, Reachability, RuntimeKind, RuntimeProfile

    runtime_kind = RuntimeKind(kind)
    return RuntimeProfile(
        id=str(runtime_kind),
        kind=runtime_kind,
        display_name="Connected over the network",
        adapter={"kind": ADAPTER_FOR_KIND[runtime_kind], "config": {"base_url": base}},
        reachability=Reachability(**reach),
    )


def host_runtime() -> Any:
    """A command in a folder: the worker's by nature, whoever can reach what."""
    from adapters.runtime import RuntimeKind, RuntimeProfile

    return RuntimeProfile(
        id="cli",
        kind=RuntimeKind.CLI,
        display_name="Runs on this machine",
        adapter={"kind": "command", "config": {"argv": ["python", "-m", "thing"], "cwd": "/somewhere"}},
    )


def provider_with(*runtimes: Any) -> Any:
    """Enough of a provider for the questions about where it runs."""
    from types import SimpleNamespace

    return SimpleNamespace(
        runtimes=[rt.model_dump(mode="json") for rt in runtimes],
        active_runtime=runtimes[0].id if runtimes else None,
        adapter=runtimes[0].adapter if runtimes else {},
    )
