"""Connect: discover an existing agent, app or service and draft a provider for it.

    target text → classify → ConnectionDiscoveryService → DiscoveryStrategy → ProviderDraft
                                                                                   ↓ confirm
                                                                                Provider

Everything here is read-only towards the target. Bevro adapts to providers;
providers never have to adapt to Bevro.
"""

from app.connect.draft import ProviderDraft
from app.connect.service import ConnectionDiscoveryService, get_discovery_service
from app.connect.targets import ConnectTarget, TargetError, classify_target

__all__ = ["ConnectTarget", "ConnectionDiscoveryService", "ProviderDraft", "TargetError", "classify_target", "get_discovery_service"]
