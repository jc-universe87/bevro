"""Maps an adapter kind ("local", "http", "mcp", ...) to an adapter instance."""

from __future__ import annotations

from adapters.base import NotSupported, ProviderAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {}


def register_adapter(adapter: ProviderAdapter) -> ProviderAdapter:
    _ADAPTERS[adapter.kind] = adapter
    return adapter


def get_adapter(kind: str) -> ProviderAdapter:
    _ensure_builtin()
    try:
        return _ADAPTERS[kind]
    except KeyError as exc:
        raise NotSupported(f"no adapter registered for kind {kind!r}") from exc


def adapter_kinds() -> list[str]:
    _ensure_builtin()
    return sorted(_ADAPTERS)


def execution_mode(adapter: ProviderAdapter, adapter_block: dict | None = None) -> str:
    """"inline" or "background" for one provider.

    Most adapters run one way. Some (MCP) depend on the provider's transport
    and answer per adapter block through an optional `execution_for`.
    """
    resolver = getattr(adapter, "execution_for", None)
    if callable(resolver):
        return str(resolver(adapter_block or {}))
    return str(getattr(adapter, "execution", "inline"))


def may_run_in_background(adapter: ProviderAdapter) -> bool:
    return getattr(adapter, "execution", "inline") == "background" or callable(getattr(adapter, "execution_for", None))


_loaded = False


def _ensure_builtin() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    # Importing registers each built-in adapter.
    from adapters import claude_code, command, http, local, mcp, openapi  # noqa: F401
