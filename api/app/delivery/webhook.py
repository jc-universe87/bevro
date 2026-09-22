"""A web address of your own: Bevro posts the event there as JSON.

The payload is deliberately small and stable (see payload.py), so whatever
sits on the other end — a chat bridge, a home dashboard, a script — keeps
working as Bevro changes.

    BEVRO_NOTIFY_WEBHOOK_URL
"""

from __future__ import annotations

import httpx

from app.config import Settings, get_settings
from app.delivery.base import DeliveryChannel, DeliveryResult

# A server that is busy or rate-limiting is worth trying again; a server that
# says "no such thing" or "not allowed" is not.
_RETRYABLE_STATUSES = {408, 425, 429}


class WebhookChannel(DeliveryChannel):
    name = "webhook"
    label = "Webhook"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate_configuration(self) -> str | None:
        url = self.default_destination()
        if not url:
            return "No web address is set up on this installation."
        if not url.lower().startswith(("http://", "https://")):
            return "The web address must start with http:// or https://."
        return None

    def default_destination(self) -> str | None:
        return self.settings.notify_webhook_url.strip() or None

    def deliver(self, payload: dict, destination: str | None) -> DeliveryResult:
        problem = self.validate_configuration()
        url = (destination or self.default_destination() or "").strip()
        if problem and not destination:
            return DeliveryResult.permanent_failure(problem)
        if not url:
            return DeliveryResult.permanent_failure("No web address to post to.")
        try:
            response = httpx.post(url, json=payload, timeout=self.settings.notify_webhook_timeout_seconds)
        except httpx.HTTPError as exc:
            return DeliveryResult.temporary_failure(f"Couldn't reach that address ({type(exc).__name__}).")
        if response.is_success:
            return DeliveryResult.sent(f"Posted to that address ({response.status_code}).")
        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            return DeliveryResult.temporary_failure(f"That address answered {response.status_code}.")
        return DeliveryResult.permanent_failure(f"That address refused the message ({response.status_code}).")
