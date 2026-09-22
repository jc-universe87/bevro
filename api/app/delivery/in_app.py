"""Bevro itself: the channel that always works.

There is nothing to configure and nothing to go wrong. The event row is the
delivery; this channel exists so that "in Bevro" is one option among the
others rather than a special case everything else has to work around.
"""

from __future__ import annotations

from app.delivery.base import ChannelHealth, DeliveryChannel, DeliveryResult


class InAppChannel(DeliveryChannel):
    name = "in_app"
    label = "In Bevro"
    external = False

    def validate_configuration(self) -> str | None:
        return None

    def health(self) -> ChannelHealth:
        return ChannelHealth("ready")

    def deliver(self, payload: dict, destination: str | None) -> DeliveryResult:
        return DeliveryResult.sent("Shown in Bevro.")
