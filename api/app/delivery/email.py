"""Email, over plain SMTP.

Configuration is ordinary mail-server settings and nothing Bevro-specific,
so any provider works: a company server, a personal account, or a local test
server such as `python -m aiosmtpd -n -l localhost:1025`.

    BEVRO_SMTP_HOST, BEVRO_SMTP_PORT, BEVRO_SMTP_USERNAME,
    BEVRO_SMTP_PASSWORD, BEVRO_SMTP_FROM, BEVRO_NOTIFY_EMAIL

None of it is required: with no mail server, Bevro still notifies in Bevro.
"""

from __future__ import annotations

import logging
import smtplib
import socket
from email.message import EmailMessage

from app.config import Settings, get_settings
from app.delivery.base import DeliveryChannel, DeliveryResult

log = logging.getLogger("bevro.delivery.email")


class SmtpChannel(DeliveryChannel):
    name = "email"
    label = "Email"
    accepts_destination = True

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ setup

    def validate_configuration(self, destination: str | None = None) -> str | None:
        s = self.settings
        if not s.smtp_host.strip():
            return "No mail server is set up on this installation."
        if not (s.smtp_from.strip() or s.smtp_username.strip()):
            return "No address to send from is set up."
        if not (destination or self.default_destination()):
            return "No address to send to is set up."
        return None

    def setup_problem(self) -> str | None:
        """A mail server and an address to send from. The recipient may come later."""
        s = self.settings
        if not s.smtp_host.strip():
            return "No mail server is set up on this installation."
        if not (s.smtp_from.strip() or s.smtp_username.strip()):
            return "No address to send from is set up."
        return None

    def default_destination(self) -> str | None:
        return self.settings.notify_email.strip() or None

    def _sender(self) -> str:
        s = self.settings
        return s.smtp_from.strip() or s.smtp_username.strip()

    # ------------------------------------------------------------------ sending

    def deliver(self, payload: dict, destination: str | None) -> DeliveryResult:
        problem = self.validate_configuration(destination)
        if problem:
            return DeliveryResult.permanent_failure(problem)
        to = (destination or self.default_destination() or "").strip()
        if not to:
            return DeliveryResult.permanent_failure("No address to send to.")

        message = self._compose(payload, to)
        s = self.settings
        try:
            with self._connect() as smtp:
                if s.smtp_starttls and s.smtp_port != 465:
                    try:
                        smtp.starttls()
                        smtp.ehlo()
                    except smtplib.SMTPNotSupportedError:
                        # The server does not offer encryption; on a local test
                        # server that is expected, so carry on.
                        log.info("mail server does not offer STARTTLS; sending without it")
                if s.smtp_username.strip():
                    smtp.login(s.smtp_username.strip(), s.smtp_password)
                smtp.send_message(message)
        except smtplib.SMTPAuthenticationError:
            return DeliveryResult.permanent_failure("The mail server rejected Bevro's username or password.")
        except smtplib.SMTPRecipientsRefused:
            return DeliveryResult.permanent_failure("The mail server would not accept that address.")
        except smtplib.SMTPSenderRefused as exc:
            return self._by_code(exc.smtp_code, "The mail server would not accept the address Bevro sends from.")
        except smtplib.SMTPResponseException as exc:
            return self._by_code(exc.smtp_code, "The mail server refused the message.")
        except smtplib.SMTPNotSupportedError:
            return DeliveryResult.permanent_failure("The mail server does not support how Bevro tried to send.")
        except (smtplib.SMTPException, OSError, socket.timeout) as exc:
            return DeliveryResult.temporary_failure(f"Couldn't reach the mail server ({type(exc).__name__}).")
        return DeliveryResult.sent(f"Emailed {to}.")

    def _connect(self):
        s = self.settings
        host, port, timeout = s.smtp_host.strip(), s.smtp_port, s.smtp_timeout_seconds
        if port == 465:
            return smtplib.SMTP_SSL(host, port, timeout=timeout)
        return smtplib.SMTP(host, port, timeout=timeout)

    @staticmethod
    def _by_code(code: int | None, message: str) -> DeliveryResult:
        """5xx means the server has decided; 4xx means try again later."""
        if code is not None and 400 <= code < 500:
            return DeliveryResult.temporary_failure(message)
        return DeliveryResult.permanent_failure(message)

    def _compose(self, payload: dict, to: str) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = payload.get("title") or "Bevro"
        message["From"] = self._sender()
        message["To"] = to
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(body(payload))
        return message


def body(payload: dict) -> str:
    """The message itself: what happened, why, and where to see it."""
    lines = [payload.get("title") or "Bevro"]
    if payload.get("reason"):
        lines += ["", payload["reason"]]
    if payload.get("summary"):
        lines += ["", payload["summary"]]
    if payload.get("url"):
        lines += ["", f"See the result in Bevro: {payload['url']}"]
    lines += ["", "— Bevro"]
    return "\n".join(lines) + "\n"
