"""MCP adapter: the provider is a Model Context Protocol server.

config:
  server_url  - a Streamable HTTP endpoint (run inline by the API), or
  argv + cwd  - a command that speaks MCP on stdin/stdout (run by the worker,
                on the machine where the command lives); `env` adds non-secret
                variables such as PYTHONPATH
  tool        - {"name": "...", "argument": "query"}: which tool takes a plain
                request and which string argument receives it. Chosen at
                discovery; editable under Advanced setup.
  auth        - {"type": "bearer", "secret": "api_key"} or absent
  timeout_seconds

Discovery uses the same client (adapters/mcp_client.py) to read the server's
identity and tools. A server whose tools all need structured input can be
connected and listed, but not asked from a plain request yet.
"""

from __future__ import annotations

import os
from typing import Any

from adapters.base import (
    ArtifactDraft,
    FailureKind,
    HealthResult,
    InvocationContext,
    InvocationRequest,
    InvocationResult,
    NotSupported,
    ProviderSpec,
    ResultState,
)
from adapters.localroots import OutsideRoots, resolve_within
from adapters.mcp_client import HttpSession, McpError, StdioSession, text_of
from adapters.registry import register_adapter
from adapters.runtime import BaseRuntimeAdapter
from adapters import urlsafety

DEFAULT_TIMEOUT = 120.0


def _headers(config: dict[str, Any], secrets: dict[str, str]) -> dict[str, str]:
    auth = config.get("auth") or {}
    token = secrets.get(auth.get("secret", "api_key")) if auth.get("type") == "bearer" else None
    return {"Authorization": f"Bearer {token}"} if token else {}


def is_stdio(config: dict[str, Any]) -> bool:
    return bool(config.get("argv")) and not config.get("server_url")


def open_session(config: dict[str, Any], secrets: dict[str, str], timeout: float):
    if is_stdio(config):
        cwd = config.get("cwd")
        if cwd:
            cwd = str(resolve_within(str(cwd)))
        env = os.environ.copy()
        for key, value in (config.get("env") or {}).items():
            env[str(key)] = str(value)
        for name in config.get("secret_env") or []:
            if name in secrets:
                env[str(name)] = secrets[name]
        return StdioSession([str(a) for a in config["argv"]], cwd=cwd, env=env, timeout=timeout)
    url = str(config.get("server_url") or "")
    if not url:
        raise McpError("no server address configured")
    # The same URL policy as everywhere else, in whichever process this runs.
    try:
        urlsafety.check(url)
    except urlsafety.UnsafeUrl as exc:
        raise McpError(str(exc)) from None
    return HttpSession(url, headers=_headers(config, secrets), timeout=timeout)


class McpAdapter(BaseRuntimeAdapter):
    kind = "mcp"
    execution = "inline"

    def execution_for(self, adapter: dict[str, Any]) -> str:
        """A stdio server lives on the host: the worker runs it. HTTP is inline."""
        return "background" if is_stdio(adapter.get("config") or {}) else "inline"

    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult:
        cfg = provider.adapter_config
        try:
            with open_session(cfg, secrets, 15.0) as session:
                session.initialize()
                tools = session.list_tools()
        except OutsideRoots as exc:
            return HealthResult(ok=False, state="unavailable", detail=f"working directory: {exc}")
        except McpError as exc:
            return HealthResult(ok=False, state="unavailable", detail=str(exc))
        from adapters.command import credential_sources

        name = session.server_info.get("name") or provider.name
        return HealthResult(ok=True, state="available", detail=f"{name}: {len(tools)} tool(s)", credentials=credential_sources(cfg, secrets) if is_stdio(cfg) else {})

    def invoke(self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult:
        context = context or InvocationContext()
        cfg = provider.adapter_config
        tool = cfg.get("tool") or {}
        name, argument = tool.get("name"), tool.get("argument")
        if not name or not argument:
            return InvocationResult(
                state=ResultState.FAILED,
                error=f"{provider.name} has tools, but none that takes a plain request. Choose one under Advanced setup.",
                failure=FailureKind.CONFIGURATION_PROBLEM,
            )
        from adapters.command import missing_secrets

        missing = missing_secrets(cfg, request.secrets)
        if missing:
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} needs a credential before it can run.", failure=FailureKind.CREDENTIAL_REQUIRED, metadata={"missing_secrets": missing})
        context.progress(f"Asking {provider.name}")
        try:
            with open_session(cfg, request.secrets, float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT))) as session:
                session.initialize()
                result = session.call_tool(str(name), {str(argument): request.request, **(tool.get("fixed_arguments") or {})})
        except OutsideRoots:
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name}'s folder is not approved on this machine.", failure=FailureKind.CONFIGURATION_PROBLEM)
        except McpError as exc:
            message = str(exc)
            if "did not answer in time" in message:
                return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} took too long to answer.", failure=FailureKind.TIMED_OUT, metadata={"mcp_error": message[:300]})
            if "couldn't read" in message:
                return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} sent back something Bevro couldn't read.", failure=FailureKind.OUTPUT_INVALID, metadata={"mcp_error": message[:300]})
            if "401" in message or "403" in message:
                return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} didn't accept Bevro's credential.", failure=FailureKind.CREDENTIAL_REQUIRED, metadata={"mcp_error": message[:300]})
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} couldn't be reached.", failure=FailureKind.PROVIDER_UNAVAILABLE, metadata={"mcp_error": message[:300]})
        text = text_of(result)
        if result.get("isError"):
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} couldn't complete this request.", failure=FailureKind.INVOCATION_FAILED, metadata={"tool_error": text[:300]})
        artifacts: list[ArtifactDraft] = []
        if text:
            artifacts.append(ArtifactDraft(type="report", title=f"Answer from {provider.name}", mime_type="text/markdown", payload={"text": text[:100_000]}))
        structured = result.get("structuredContent")
        if isinstance(structured, (dict, list)):
            artifacts.append(ArtifactDraft(type="structured", title="Details", payload=structured))
        summary = _summary(text) or "Done."
        return InvocationResult(state=ResultState.COMPLETED, summary=summary, artifacts=artifacts, metadata={"tool": name})

    def get_status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        raise NotSupported("MCP tool calls complete synchronously")

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool:
        raise NotSupported("MCP tool calls cannot be cancelled once started")


def _summary(text: str, limit: int = 200) -> str | None:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first = first.lstrip("#*- ").strip()
    if not first:
        return None
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0] + "…"
    return first


register_adapter(McpAdapter())
