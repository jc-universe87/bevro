"""How Bevro tells someone something, without knowing where it goes.

A channel is asked three things:

    validate_configuration()  is this usable on this installation, and if not, why
    health()                  is it usable right now
    deliver(payload, where)   send this, and say plainly how it went

Nothing above this line knows about mail servers or HTTP. An automation says
"tell me in Bevro and by email"; the channel decides what that means.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryResult:
    """How one attempt went.

    `permanent` is the important one: a wrong password or a rejected address
    will be just as wrong in half an hour, so Bevro stops rather than
    knocking on the same door for ever.
    """

    ok: bool
    detail: str = ""
    permanent: bool = False

    @classmethod
    def sent(cls, detail: str = "") -> "DeliveryResult":
        return cls(ok=True, detail=detail)

    @classmethod
    def temporary_failure(cls, detail: str) -> "DeliveryResult":
        return cls(ok=False, detail=detail, permanent=False)

    @classmethod
    def permanent_failure(cls, detail: str) -> "DeliveryResult":
        return cls(ok=False, detail=detail, permanent=True)


@dataclass(frozen=True)
class ChannelHealth:
    # "ready" | "not_configured" | "unavailable"
    state: str
    note: str | None = None

    @property
    def usable(self) -> bool:
        return self.state == "ready"


class DeliveryChannel(ABC):
    """One way of reaching a person."""

    # Stable identifier stored on delivery rows: "in_app", "email", "webhook".
    name: str = ""
    # What the person sees: "In Bevro", "Email".
    label: str = ""
    # False for in-app: nothing leaves this installation.
    external: bool = True

    @abstractmethod
    def validate_configuration(self) -> str | None:
        """None when this channel can be used; otherwise why it cannot, in words."""

    def health(self) -> ChannelHealth:
        problem = self.validate_configuration()
        return ChannelHealth("not_configured", problem) if problem else ChannelHealth("ready")

    def default_destination(self) -> str | None:
        """Where this installation sends by default. None when it has nowhere."""
        return None

    @abstractmethod
    def deliver(self, payload: dict, destination: str | None) -> DeliveryResult:
        """Send one event. Never raises: a failure is a result, not an exception."""
