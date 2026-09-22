"""Local adapter: the provider is a Python callable in this process.

`ref` is "module.path:function". The function takes an InvocationRequest and
a ProviderSpec and returns an InvocationResult. This is what the example
providers use; it is also how a self-hosted daemon that ships its own Python
shim can plug in.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable

from adapters.base import (
    FailureKind,
    HealthResult,
    InvocationContext,
    InvocationRequest,
    InvocationResult,
    NotSupported,
    ProviderSpec,
    ResultState,
)
from adapters.registry import register_adapter
from adapters.runtime import BaseRuntimeAdapter

Handler = Callable[[InvocationRequest, ProviderSpec], InvocationResult]


def _resolve(ref: str) -> Handler:
    if ":" not in ref:
        raise NotSupported(f"local adapter ref must look like 'module:function', got {ref!r}")
    module_name, attr = ref.split(":", 1)
    module = importlib.import_module(module_name)
    handler = getattr(module, attr, None)
    if not callable(handler):
        raise NotSupported(f"{ref!r} is not callable")
    return handler


class LocalAdapter(BaseRuntimeAdapter):
    kind = "local"

    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult:
        try:
            _resolve(provider.adapter_ref)
        except (NotSupported, ImportError) as exc:
            return HealthResult(ok=False, detail=str(exc))
        return HealthResult(ok=True)

    def invoke(self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult:
        try:
            handler = _resolve(provider.adapter_ref)
        except (NotSupported, ImportError) as exc:
            return InvocationResult(state=ResultState.FAILED, error=str(exc), failure=FailureKind.CONFIGURATION_PROBLEM)
        if len(inspect.signature(handler).parameters) >= 3:
            return handler(request, provider, context)
        return handler(request, provider)

    def get_status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        raise NotSupported("local providers complete synchronously")

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool:
        raise NotSupported("local providers cannot be cancelled once started")


register_adapter(LocalAdapter())
