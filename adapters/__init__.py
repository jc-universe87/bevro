"""The boundary between Bevro and the things that do work.

Bevro is not an agent framework. It never runs an agent loop itself; it asks
a provider to do something through one of these adapters and records what
came back as a task, a run and some artifacts.

This package depends on nothing inside the Bevro API so that the contract can
be read (and implemented) on its own.
"""

from adapters.base import (
    ArtifactDraft,
    FailureKind,
    HealthResult,
    InputRequest,
    InvocationContext,
    InvocationRequest,
    InvocationResult,
    NotSupported,
    ProviderAdapter,
    ProviderSpec,
    ResultState,
)
from adapters.registry import get_adapter, register_adapter
from adapters.runtime import CredentialStrategy, HealthState, InputMode, OutputMode, RuntimeAbilities, RuntimeAdapter, RuntimeAttempt, RuntimeHealth, RuntimeKind, RuntimeProfile

__all__ = [
    "ArtifactDraft",
    "FailureKind",
    "HealthResult",
    "InputRequest",
    "InvocationContext",
    "InvocationRequest",
    "InvocationResult",
    "NotSupported",
    "ProviderAdapter",
    "ProviderSpec",
    "ResultState",
    "get_adapter",
    "register_adapter",
    "CredentialStrategy",
    "HealthState",
    "RuntimeAttempt",
    "RuntimeHealth",
    "InputMode",
    "OutputMode",
    "RuntimeAbilities",
    "RuntimeAdapter",
    "RuntimeKind",
    "RuntimeProfile",
]
