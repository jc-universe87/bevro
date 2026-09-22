"""MCP discovery: ask the server who it is and what tools it has.

`initialize` and `tools/list` are the protocol's own read-only introduction.
Tool descriptions become capabilities; the tool that takes one plain string
becomes the way Bevro asks the server something.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from adapters.mcp_client import HttpSession, McpError, StdioSession
from app.connect.capabilities import capability_from_tool
from app.connect.draft import DraftAuth, DraftCapability, ProviderDraft
from app.connect.inspect import humanise
from app.connect.runtimes import mcp_runtime
from adapters.runtime import CredentialStrategy, Credentials
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed
from app.connect.targets import ConnectTarget

ASK_NAMES = ("ask", "query", "search", "research", "run", "chat", "answer", "prompt", "complete", "generate", "execute", "task")
STRING_ARGS = ("query", "prompt", "question", "request", "input", "text", "topic", "message", "task", "goal", "q")


def choose_tool(tools: list[dict[str, Any]]) -> tuple[str, str] | None:
    """(tool name, argument name) for the tool that takes one plain request."""
    best: tuple[int, str, str] | None = None
    for tool in tools:
        name = str(tool.get("name") or "")
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = [r for r in (schema.get("required") or []) if isinstance(r, str)]
        strings = [p for p, s in props.items() if isinstance(s, dict) and s.get("type", "string") == "string"]
        if not strings:
            continue
        other_required = [r for r in required if r not in strings]
        if other_required:
            continue
        arg = next((a for a in STRING_ARGS if a in strings), None)
        if arg is None and len(required) == 1 and required[0] in strings:
            arg = required[0]
        if arg is None and len(strings) == 1 and len(props) == 1:
            arg = strings[0]
        if arg is None:
            continue
        score = 0
        if any(k in name.lower() for k in ASK_NAMES):
            score += 2
        if len(required) <= 1:
            score += 1
        if len(props) == 1:
            score += 1
        if best is None or score > best[0]:
            best = (score, name, arg)
    return (best[1], best[2]) if best else None


def draft_from_session(session, label: str, adapter: dict[str, Any], mechanism_label: str, availability: str, auth: DraftAuth, evidence: list[str]) -> ProviderDraft:
    session.initialize()
    tools = session.list_tools()
    info = session.server_info
    name = str(info.get("name") or "MCP server")
    version = f" {info['version']}" if info.get("version") else ""
    description = " ".join((session.instructions or "").split())[:2000]
    capabilities: list[DraftCapability] = [capability_from_tool(str(t["name"]), t.get("description")) for t in tools[:8]]
    chosen = choose_tool(tools)
    warnings: list[str] = []
    config = dict(adapter.get("config") or {})
    if chosen:
        config["tool"] = {"name": chosen[0], "argument": chosen[1]}
        evidence = [*evidence, f"Tool '{chosen[0]}' takes a plain request"]
    else:
        config["tool"] = {}
        warnings.append("Its tools need structured input, so Bevro can list them but can't ask it from a plain request yet.")
    adapter = {**adapter, "config": config}
    tool_notes = [f"{t.get('name')}: {' '.join(str(t.get('description') or '').split())[:160]}" for t in tools[:10]]
    stdio = bool(config.get("argv")) and not config.get("server_url")
    if auth.required:
        creds = Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=[auth.secret_name or "api_key"], required_from_user=True, note=auth.hint)
    elif auth.secret_name:
        creds = Credentials(strategy=CredentialStrategy.PROJECT_DOTENV if config.get("self_configured") else CredentialStrategy.INHERITED_ENVIRONMENT, names=[auth.secret_name], note=auth.hint)
    else:
        creds = Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED if not stdio else CredentialStrategy.NONE)
    all_evidence = [f"MCP server '{name}'{version} with {len(tools)} tool(s)", *evidence]
    draft = ProviderDraft(
        name=humanise(name) if "-" in name or "_" in name else name[:120],
        description=description,
        capabilities=capabilities,
        mechanism="mcp",
        mechanism_label=mechanism_label,
        adapter=adapter,
        invocation_label=label,
        availability=availability,  # type: ignore[arg-type]
        confidence="high" if chosen else "medium",
        evidence=all_evidence,
        warnings=warnings,
        auth=auth,
        invocable=bool(chosen),
        assist_evidence={"tools": tool_notes},
    )
    rt = mcp_runtime("mcp", stdio=stdio, adapter=adapter, display_name="Uses MCP on this machine" if stdio else "Uses MCP over the network", availability=availability, confidence="high" if chosen else "medium", credentials=creds, evidence=all_evidence, warnings=warnings, target=str(config.get("cwd") or config.get("server_url") or "") or None)
    return draft.with_runtimes([rt])


class McpDiscoveryStrategy:
    """Only reached for HTTP MCP endpoints and for stdio servers being tested."""

    name = "mcp"

    def supports(self, target: ConnectTarget) -> bool:
        return False  # the HTTP strategy calls discover_url; local strategy uses refine_stdio

    def discover(self, target: ConnectTarget, context: DiscoveryContext) -> ProviderDraft:  # pragma: no cover
        return self.discover_url(target.value, context, {})

    def discover_url(self, url: str, context: DiscoveryContext, headers: dict[str, str]) -> ProviderDraft:
        auth = DraftAuth()
        config: dict[str, Any] = {"server_url": url}
        if "Authorization" in headers:
            config["auth"] = {"type": "bearer", "secret": "api_key"}
            auth = DraftAuth(required=True, secret_name="api_key", label="API token", hint="Sent as a bearer token. Stored encrypted; never shown again.")
        try:
            with HttpSession(url, headers=headers, timeout=context.timeout, transport=context.transport) as session:
                return draft_from_session(session, url, {"kind": "mcp", "config": config}, "MCP server", "ready", auth, [f"Reached over HTTP at {urlsplit(url).hostname}"])
        except McpError as exc:
            message = str(exc)
            if "401" in message or "403" in message:
                raise DiscoveryFailed("The MCP server wants authentication. Add its token and try again.") from exc
            raise DiscoveryFailed(f"Not an MCP server, or it did not answer: {message}") from exc


def refine_stdio(draft: ProviderDraft, secrets: dict[str, str]) -> ProviderDraft:
    """Start a local MCP server once (Test connection), read its tools, keep the draft's paths."""
    import os

    config = dict(draft.adapter.get("config") or {})
    env = os.environ.copy()
    for key, value in (config.get("env") or {}).items():
        env[str(key)] = str(value)
    for name in config.get("secret_env") or []:
        if name in secrets:
            env[name] = secrets[name]
    try:
        with StdioSession([str(a) for a in config["argv"]], cwd=config.get("cwd"), env=env, timeout=20.0) as session:
            refined = draft_from_session(session, draft.invocation_label or "", draft.adapter, draft.mechanism_label or "Local MCP server", "needs_worker", draft.auth, list(draft.evidence))
    except McpError as exc:
        raise DiscoveryFailed(f"The server started but didn't speak MCP: {exc}") from exc
    if not refined.description and draft.description:
        refined.description = draft.description
    if not refined.capabilities:
        refined.capabilities = draft.capabilities
    if draft.name:
        refined.name = draft.name  # the project's own name beats the server's internal one
    return refined
