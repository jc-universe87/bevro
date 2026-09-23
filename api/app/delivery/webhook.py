"""A web address of your own: Bevro posts the event there as JSON.

The payload is deliberately small and stable (see payload.py), so whatever
sits on the other end - a chat bridge, a home dashboard, a script - keeps
working as Bevro changes.

    BEVRO_NOTIFY_WEBHOOK_URL       where to post, unless a piece of work says otherwise
    BEVRO_NOTIFY_WEBHOOK_SECRET    optional: sign every post so the receiver can check it

Signing follows the usual shape. Each request carries:

    X-Bevro-Event:      automation.matched
    X-Bevro-Timestamp:  1774000000
    X-Bevro-Signature:  sha256=<hex>

where the signature is HMAC-SHA256 over the exact bytes `<timestamp>.<body>`.
The timestamp is signed as well as sent, so an old message cannot be replayed
with a fresh one's clothes on. Verifying is a few lines in any language; see
docs/NOTIFICATIONS.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx

from app.config import Settings, get_settings
from app.delivery.base import DeliveryChannel, DeliveryResult

# A server that is busy or rate-limiting is worth trying again; a server that
# says "no such thing" or "not allowed" is not.
_RETRYABLE_STATUSES = {408, 425, 429}

SIGNATURE_HEADER = "X-Bevro-Signature"
TIMESTAMP_HEADER = "X-Bevro-Timestamp"
EVENT_HEADER = "X-Bevro-Event"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """The value of X-Bevro-Signature for this body at this moment."""
    signed = f"{timestamp}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(secret: str, timestamp: str, body: bytes, signature: str, *, tolerance_seconds: int = 300) -> bool:
    """What a receiver does. Here so the documented recipe is also tested."""
    try:
        age = abs(time.time() - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > tolerance_seconds:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature or "")


class WebhookChannel(DeliveryChannel):
    name = "webhook"
    label = "Webhook"
    accepts_destination = True

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate_configuration(self, destination: str | None = None) -> str | None:
        url = (destination or self.default_destination() or "").strip()
        if not url:
            return "No web address is set up on this installation."
        if not url.lower().startswith(("http://", "https://")):
            return "The web address must start with http:// or https://."
        return None

    def setup_problem(self) -> str | None:
        """Nothing to set up: any address Bevro is given will do."""
        return None

    def default_destination(self) -> str | None:
        return self.settings.notify_webhook_url.strip() or None

    def deliver(self, payload: dict, destination: str | None) -> DeliveryResult:
        problem = self.validate_configuration(destination)
        if problem:
            return DeliveryResult.permanent_failure(problem)
        url = (destination or self.default_destination() or "").strip()

        # The exact bytes are signed, so they are also the exact bytes sent.
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        headers = {"Content-Type": "application/json", EVENT_HEADER: str(payload.get("event") or "")}
        secret = self.settings.notify_webhook_secret.strip()
        if secret:
            timestamp = str(int(time.time()))
            headers[TIMESTAMP_HEADER] = timestamp
            headers[SIGNATURE_HEADER] = sign(secret, timestamp, body)

        try:
            response = httpx.post(url, content=body, headers=headers, timeout=self.settings.notify_webhook_timeout_seconds)
        except httpx.HTTPError as exc:
            return DeliveryResult.temporary_failure(f"Couldn't reach that address ({type(exc).__name__}).")
        if response.is_success:
            return DeliveryResult.sent(f"Posted to that address ({response.status_code}).")
        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            return DeliveryResult.temporary_failure(f"That address answered {response.status_code}.")
        return DeliveryResult.permanent_failure(f"That address refused the message ({response.status_code}).")
