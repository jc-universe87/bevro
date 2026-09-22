"""Delivery: how a person hears about a result that mattered.

    NotificationEvent → channels → in Bevro, by email, to a web address

Bevro decides *that* something is worth saying (services/notifications.py).
A channel decides *how* it is said. Adding one means writing a class here and
listing it below; nothing about automations, tasks or providers changes.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.delivery.base import ChannelHealth, DeliveryChannel, DeliveryResult
from app.delivery.email import SmtpChannel
from app.delivery.in_app import InAppChannel
from app.delivery.payload import event_payload, scrub
from app.delivery.webhook import WebhookChannel

# In Bevro first, then out. The order is the order things are tried in.
CHANNEL_CLASSES: tuple[type[DeliveryChannel], ...] = (InAppChannel, SmtpChannel, WebhookChannel)

IN_APP = InAppChannel.name
EXTERNAL_CHANNELS = tuple(cls.name for cls in CHANNEL_CLASSES if cls.external)


def channels(settings: Settings | None = None) -> dict[str, DeliveryChannel]:
    """Every channel Bevro knows how to use, configured or not."""
    settings = settings or get_settings()
    built: dict[str, DeliveryChannel] = {}
    for cls in CHANNEL_CLASSES:
        try:
            built[cls.name] = cls(settings) if cls is not InAppChannel else cls()
        except TypeError:
            built[cls.name] = cls()
    return built


def get_channel(name: str, settings: Settings | None = None) -> DeliveryChannel | None:
    return channels(settings).get(name)


def availability(settings: Settings | None = None) -> list[dict]:
    """What the browser may offer, in words. No host names, no passwords."""
    out = []
    for name, channel in channels(settings).items():
        health = channel.health()
        out.append(
            {
                "name": name,
                "label": channel.label,
                "available": health.usable,
                "external": channel.external,
                "note": health.note,
            }
        )
    return out


__all__ = [
    "CHANNEL_CLASSES",
    "ChannelHealth",
    "DeliveryChannel",
    "DeliveryResult",
    "EXTERNAL_CHANNELS",
    "IN_APP",
    "InAppChannel",
    "SmtpChannel",
    "WebhookChannel",
    "availability",
    "channels",
    "event_payload",
    "get_channel",
    "scrub",
]
