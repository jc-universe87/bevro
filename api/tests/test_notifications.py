"""Telling the person: what is worth saying, how it goes out, and what happens
when it cannot.

No real mail server and no real web address: the SMTP transport is a fake
that records what it was given, and the webhook channel is handed a stub.
A fixed clock throughout, so backoff is counted rather than waited for.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone

import pytest

from adapters import InvocationResult, ResultState
from app.automations.conditions import ConditionKind, ConditionSpec
from app.automations.schedule import Recurrence, ScheduleSpec
from app.config import Settings
from app.delivery import availability, channels
from app.delivery.base import DeliveryChannel, DeliveryResult
from app.delivery.email import SmtpChannel, body
from app.delivery.payload import event_payload, scrub
from app.delivery.webhook import WebhookChannel
from app.models import NotificationDelivery, NotificationEvent
from app.services import automations as automation_service
from app.services import notifications as notification_service
from app.services import providers as provider_service
from app.services import tasks as task_service

NOW = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- helpers

@pytest.fixture
def research(seeded):
    return provider_service.get_by_slug(seeded, "research")


def daily() -> ScheduleSpec:
    from datetime import time

    return ScheduleSpec(recurrence=Recurrence.DAILY, at=time(9, 0), timezone="UTC")


def make(seeded, *, mode="monitoring", condition=None, notify=None, provider=None, instruction="Watch the competitor page"):
    automation = automation_service.create(
        seeded,
        instruction=instruction,
        schedule=daily(),
        mode=mode,
        condition=condition or (ConditionSpec(kind=ConditionKind.CHANGED) if mode == "monitoring" else None),
        provider=provider,
        notify=notify,
        now=NOW - timedelta(days=1),
    )
    seeded.commit()
    return automation


def occur(seeded, automation, at: datetime, *, model=None):
    """One whole occurrence: claim, run it, settle it."""
    for claimed, run in automation_service.claim_due(seeded, now=at):
        task = automation_service.start_run(seeded, claimed, run)
        seeded.commit()
        if task is not None and task.runs and task.runs[-1].state == "pending":
            task_service.execute_run(seeded, task.runs[-1].id)
    return automation_service.settle_finished(seeded, model=model)


def answers(monkeypatch, *replies: str):
    stream = iter(replies)
    monkeypatch.setattr(task_service, "execute", lambda *a, **k: (InvocationResult(state=ResultState.COMPLETED, summary=next(stream)), []))


def settings_with(**kwargs) -> Settings:
    base = {
        "smtp_host": "",
        "notify_email": "",
        "notify_webhook_url": "",
        "app_url": "http://localhost:6140",
    }
    return Settings(**{**base, **kwargs})


EMAIL_READY = dict(smtp_host="mail.example", smtp_port=587, smtp_from="bevro@example", notify_email="me@example")


class FakeSmtp:
    """Stands in for smtplib.SMTP. Records what it was asked to send."""

    sent: list[dict] = []
    fail_with: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.logged_in: tuple[str, str] | None = None

    def __enter__(self):
        if type(self).fail_with is not None:
            raise type(self).fail_with
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        return None

    def ehlo(self):
        return None

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        type(self).sent.append(
            {"to": message["To"], "from": message["From"], "subject": message["Subject"], "body": message.get_content()}
        )


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSmtp.sent = []
    FakeSmtp.fail_with = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr("app.delivery.email.smtplib.SMTP", FakeSmtp)
    return FakeSmtp


# --------------------------------------------------------------------------- what is worth saying

def test_a_matching_monitoring_run_is_worth_telling_someone(seeded, research, monkeypatch):
    automation = make(seeded, provider=research)
    answers(monkeypatch, "Three competitors listed.", "Five competitors listed, including Acme.")

    occur(seeded, automation, NOW)  # first look: the starting point
    assert notification_service.listing(seeded) == []

    occur(seeded, automation, NOW + timedelta(days=1))
    events = notification_service.listing(seeded)
    assert len(events) == 1
    assert events[0].kind == "automation.matched"
    assert events[0].title == automation.title
    assert "different" in (events[0].reason or "")
    assert events[0].read_at is None


def test_a_quiet_run_says_nothing_at_all(seeded, research, monkeypatch):
    automation = make(seeded, provider=research)
    answers(monkeypatch, "Three competitors listed.", "Three competitors listed.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    last = automation_service.last_run(seeded, automation)
    assert last.matched is False and last.outcome == "completed"
    assert notification_service.listing(seeded) == []
    assert notification_service.unread_count(seeded) == 0


def test_ordinary_scheduled_work_goes_to_recent_and_nowhere_else(seeded, research, monkeypatch):
    automation = make(seeded, mode="scheduled", provider=research)
    answers(monkeypatch, "Done.")
    occur(seeded, automation, NOW)

    assert automation_service.last_run(seeded, automation).outcome == "completed"
    assert notification_service.listing(seeded) == []  # the result is in Recent, as always


def test_scheduled_work_can_be_asked_to_say_when_it_finishes(seeded, research, monkeypatch):
    automation = make(seeded, mode="scheduled", provider=research, notify={"on_finish": True})
    answers(monkeypatch, "Done, and here is the report.")
    occur(seeded, automation, NOW)

    events = notification_service.listing(seeded)
    assert [e.kind for e in events] == ["automation.finished"]
    assert events[0].task_id is not None


# --------------------------------------------------------------------------- in Bevro

def test_unread_then_read_and_nothing_else_clears_it(seeded, research, monkeypatch):
    automation = make(seeded, provider=research)
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    assert notification_service.unread_count(seeded) == 1
    # Merely looking at the list changes nothing.
    notification_service.listing(seeded)
    assert notification_service.unread_count(seeded) == 1

    event = notification_service.listing(seeded)[0]
    notification_service.mark_read(seeded, event)
    seeded.commit()
    assert notification_service.unread_count(seeded) == 0
    assert notification_service.listing(seeded, unread_only=True) == []


def test_in_bevro_is_a_delivery_like_any_other(seeded, research, monkeypatch):
    automation = make(seeded, provider=research)
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    event = notification_service.listing(seeded)[0]
    assert [d.channel for d in event.deliveries] == ["in_app"]
    assert event.deliveries[0].status == "sent"
    assert notification_service.delivery_state(event) == "in_app"


# --------------------------------------------------------------------------- email

def test_without_a_mail_server_email_is_not_offered(seeded):
    offered = {c["name"]: c for c in availability(settings_with())}
    assert offered["in_app"]["available"] is True
    assert offered["email"]["available"] is False
    assert "mail server" in offered["email"]["note"]
    assert offered["webhook"]["available"] is False


def test_with_a_mail_server_email_is_offered(seeded):
    offered = {c["name"]: c for c in availability(settings_with(**EMAIL_READY))}
    assert offered["email"]["available"] is True and offered["email"]["note"] is None


def test_asking_for_email_that_is_not_set_up_says_so_and_sends_nothing(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with()
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    event = notification_service.listing(seeded)[0]
    email = next(d for d in event.deliveries if d.channel == "email")
    assert email.status == "failed" and email.attempts == 0
    assert "mail server" in email.last_error
    assert fake_smtp.sent == []
    # The event is still there, in Bevro, which always works.
    assert notification_service.unread_count(seeded) == 1


def test_a_configured_email_is_sent_once_with_a_link_back(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY, smtp_username="bevro", smtp_password="hunter2")
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "Three competitors.", "Five competitors, including Acme.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    assert notification_service.deliver_pending(seeded, settings=settings) == 1
    assert len(fake_smtp.sent) == 1
    message = fake_smtp.sent[0]
    assert message["to"] == "me@example" and message["from"] == "bevro@example"
    assert message["subject"] == automation.title
    assert "different" in message["body"]
    assert "/tasks/" in message["body"]

    event = notification_service.listing(seeded)[0]
    assert notification_service.delivery_state(event) == "delivered"
    # A second turn of the scheduler must not send it again.
    assert notification_service.deliver_pending(seeded, settings=settings) == 0
    assert len(fake_smtp.sent) == 1


def test_a_temporary_mail_problem_is_tried_again_later(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    fake_smtp.fail_with = ConnectionRefusedError("mail server is down")
    assert notification_service.deliver_pending(seeded, settings=settings, now=NOW) == 0
    event = notification_service.listing(seeded)[0]
    email = next(d for d in event.deliveries if d.channel == "email")
    assert email.status == "pending" and email.attempts == 1
    assert email.next_attempt_at == NOW + timedelta(seconds=60)
    assert "Couldn't reach the mail server" in email.last_error
    # The person is told a try failed, even while Bevro intends another.
    event = notification_service.listing(seeded)[0]
    assert "will try again" in notification_service.delivery_problem(event)

    # Not yet due: nothing is attempted.
    assert notification_service.claim_deliveries(seeded, now=NOW + timedelta(seconds=30)) == []

    # When it is due and the server is back, it goes.
    fake_smtp.fail_with = None
    assert notification_service.deliver_pending(seeded, settings=settings, now=NOW + timedelta(seconds=61)) == 1
    assert len(fake_smtp.sent) == 1


def test_bevro_stops_after_a_few_tries(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    fake_smtp.fail_with = ConnectionRefusedError("still down")
    moment = NOW
    for wait in (*notification_service.RETRY_BACKOFF_SECONDS, 0):
        notification_service.deliver_pending(seeded, settings=settings, now=moment)
        moment += timedelta(seconds=wait + 1)
    event = notification_service.listing(seeded)[0]
    email = next(d for d in event.deliveries if d.channel == "email")
    assert email.attempts == notification_service.MAX_ATTEMPTS
    assert email.status == "failed" and "stopped trying" in email.last_error
    assert notification_service.delivery_state(event) == "delivery_failed"


def test_a_wrong_password_is_not_tried_again(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY, smtp_username="bevro", smtp_password="wrong")
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    fake_smtp.fail_with = smtplib.SMTPAuthenticationError(535, b"auth failed")
    notification_service.deliver_pending(seeded, settings=settings, now=NOW)
    event = notification_service.listing(seeded)[0]
    email = next(d for d in event.deliveries if d.channel == "email")
    assert email.status == "failed" and email.attempts == 1
    assert "username or password" in email.last_error
    # Nothing is waiting: Bevro does not knock on that door again.
    assert notification_service.claim_deliveries(seeded, now=NOW + timedelta(hours=2)) == []


def test_a_rejected_address_is_a_permanent_failure(seeded):
    channel = SmtpChannel(settings_with(**EMAIL_READY))
    assert channel.validate_configuration() is None
    result = channel._by_code(550, "refused")
    assert result.permanent is True and result.ok is False
    assert channel._by_code(451, "busy").permanent is False


def test_a_failed_email_can_be_tried_again_by_hand(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    fake_smtp.fail_with = smtplib.SMTPAuthenticationError(535, b"auth failed")
    notification_service.deliver_pending(seeded, settings=settings, now=NOW)
    event = notification_service.listing(seeded)[0]

    fake_smtp.fail_with = None
    assert notification_service.retry_failed(seeded, event, now=NOW) == 1
    seeded.commit()
    assert notification_service.deliver_pending(seeded, settings=settings, now=NOW) == 1
    assert len(fake_smtp.sent) == 1


# --------------------------------------------------------------------------- the work is not affected

def test_the_work_stays_successful_when_delivery_fails(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    calls = {"n": 0}

    def run(*a, **k):
        calls["n"] += 1
        return InvocationResult(state=ResultState.COMPLETED, summary=f"Answer {calls['n']}."), []

    monkeypatch.setattr(task_service, "execute", run)
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    fake_smtp.fail_with = smtplib.SMTPAuthenticationError(535, b"nope")
    notification_service.deliver_pending(seeded, settings=settings, now=NOW)

    run_row = automation_service.last_run(seeded, automation)
    task = task_service.get_task(seeded, run_row.task_id)
    assert task.state == "completed"
    assert run_row.outcome == "completed" and run_row.matched is True
    event = notification_service.listing(seeded)[0]
    assert notification_service.delivery_state(event) == "delivery_failed"
    # The provider ran twice - once per occurrence - and not a third time.
    assert calls["n"] == 2


# --------------------------------------------------------------------------- one event, one message

def test_an_occurrence_is_announced_once_however_many_schedulers_there_are(seeded, research, monkeypatch):
    automation = make(seeded, provider=research)
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    run = automation_service.last_run(seeded, automation)
    task = task_service.get_task(seeded, run.task_id)
    # A second scheduler settling the same occurrence, at the same moment.
    again = notification_service.raise_event(
        seeded, kind="automation.matched", title=automation.title, automation=automation, run=run, task=task
    )
    seeded.commit()
    assert again is None
    assert len(notification_service.listing(seeded)) == 1


def test_a_delivery_is_claimed_before_it_is_sent(seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    first = notification_service.claim_deliveries(seeded, now=NOW)
    assert [d.channel for d in first] == ["email"]
    # A second sender arriving now finds nothing to do.
    assert notification_service.claim_deliveries(seeded, now=NOW) == []
    assert first[0].status == "sending"


# --------------------------------------------------------------------------- what leaves Bevro

def test_nothing_private_leaves_in_a_payload(seeded, research, monkeypatch):
    automation = make(seeded, provider=research)
    leaky = (
        "Report written to /srv/api/data/artifacts/report.md and ~/agents/secret/notes.txt. "
        "OPENAI_API_KEY=sk-live-abcdefghijklmnopqrstuvwx. "
        "See https://example.com/competitors/list for the source."
    )
    answers(monkeypatch, "Nothing yet.", leaky)
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    event = notification_service.listing(seeded)[0]
    payload = event_payload(event, app_url="http://localhost:6140")
    text = str(payload)
    assert "/srv/" not in text and "/artifacts/" not in text and "~/agents" not in text
    assert "sk-live-abcdefghijklmnopqrstuvwx" not in text
    assert "OPENAI_API_KEY: …" in text  # the name may stay; the value may not
    assert "https://example.com/competitors/list" in text  # a web address is not a path
    assert set(payload) == {"event", "title", "summary", "reason", "task_id", "automation_id", "created_at", "url"}
    assert payload["url"].endswith(f"/tasks/{event.task_id}")


def test_the_scrubber_leaves_ordinary_prose_alone():
    plain = "Five competitors were found. The biggest change is Acme's new pricing page."
    assert scrub(plain) == plain
    assert scrub("x" * 900).endswith("…")
    assert scrub(None) is None


def test_the_email_body_says_what_happened_and_where_to_look():
    payload = {"title": "Competitor watch", "reason": "The result is different from last time.", "summary": "Five competitors.", "url": "http://localhost:6140/tasks/abc"}
    text = body(payload)
    assert "Competitor watch" in text and "different from last time" in text
    assert "http://localhost:6140/tasks/abc" in text


# --------------------------------------------------------------------------- webhook

def test_a_webhook_posts_the_small_payload(seeded, research, monkeypatch):
    settings = settings_with(notify_webhook_url="https://hooks.example/bevro")
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    posted: list[tuple[str, dict]] = []

    class Response:
        status_code = 200
        is_success = True

    monkeypatch.setattr("app.delivery.webhook.httpx.post", lambda url, json, timeout: (posted.append((url, json)), Response())[1])
    automation = make(seeded, provider=research, notify={"webhook": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))

    assert notification_service.deliver_pending(seeded, settings=settings, now=NOW) == 1
    url, payload = posted[0]
    assert url == "https://hooks.example/bevro"
    assert payload["event"] == "automation.matched"
    assert set(payload) == {"event", "title", "summary", "reason", "task_id", "automation_id", "created_at", "url"}


def test_a_webhook_tells_a_busy_server_from_a_wrong_address(monkeypatch):
    channel = WebhookChannel(settings_with(notify_webhook_url="https://hooks.example/bevro"))

    def answer(status):
        class Response:
            status_code = status
            is_success = 200 <= status < 300

        monkeypatch.setattr("app.delivery.webhook.httpx.post", lambda url, json, timeout: Response())

    answer(503)
    assert channel.deliver({}, None).permanent is False
    answer(404)
    assert channel.deliver({}, None).permanent is True
    answer(429)
    assert channel.deliver({}, None).permanent is False


# --------------------------------------------------------------------------- the browser's view

def test_the_browser_sees_what_happened_and_never_where_it_went(client, seeded, research, monkeypatch, fake_smtp):
    settings = settings_with(**EMAIL_READY)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    automation = make(seeded, provider=research, notify={"email": True})
    answers(monkeypatch, "One.", "Two.")
    occur(seeded, automation, NOW)
    occur(seeded, automation, NOW + timedelta(days=1))
    notification_service.deliver_pending(seeded, settings=settings)

    listed = client.get("/api/notifications").json()
    assert listed["unread"] == 1 and len(listed["items"]) == 1
    item = listed["items"][0]
    assert item["read"] is False and item["kind"] == "automation.matched"
    assert item["channels"] == ["email", "in_app"] and item["delivery"] == "delivered"
    body_text = str(listed)
    assert "me@example" not in body_text and "mail.example" not in body_text
    assert "hunter2" not in body_text and "/srv" not in body_text

    # Looking at the list did not mark it read; saying so does.
    assert client.get("/api/notifications").json()["unread"] == 1
    read = client.post(f"/api/notifications/{item['id']}/read").json()
    assert read["read"] is True
    assert client.get("/api/notifications").json()["unread"] == 0


def test_the_browser_is_told_which_channels_this_installation_has(client, monkeypatch):
    listed = client.get("/api/notifications/channels").json()
    by_name = {c["name"]: c for c in listed}
    assert by_name["in_app"]["available"] is True
    assert by_name["email"]["available"] is False and "mail server" in by_name["email"]["note"]
    assert all("smtp" not in str(c).lower() for c in listed)


def test_an_automation_carries_its_own_choice_of_channels(client, seeded, research):
    created = client.post(
        "/api/automations",
        json={"when": "every day at 9", "instruction": "Watch the page", "only_when": "only when something changes", "notify": {"in_app": True, "email": True}},
    ).json()
    assert created["notify"] == {"in_app": True, "email": True, "webhook": False, "on_finish": False}

    updated = client.patch(f"/api/automations/{created['id']}", json={"notify": {"in_app": True, "email": False}}).json()
    assert updated["notify"]["email"] is False


# --------------------------------------------------------------------------- the channel contract

def test_every_channel_answers_the_same_three_questions():
    for name, channel in channels(settings_with()).items():
        assert isinstance(channel, DeliveryChannel) and channel.name == name and channel.label
        assert channel.validate_configuration() is None or isinstance(channel.validate_configuration(), str)
        health = channel.health()
        assert health.state in ("ready", "not_configured", "unavailable")
    assert channels(settings_with())["in_app"].deliver({}, None) == DeliveryResult(ok=True, detail="Shown in Bevro.")


def test_an_automation_notifies_in_bevro_unless_it_is_told_otherwise(seeded):
    assert notification_service.preferences(None) == {"in_app": True, "email": False, "webhook": False, "on_finish": False}
    assert notification_service.wanted_channels(notification_service.preferences({"email": True})) == ["in_app", "email"]
    assert notification_service.wanted_channels(notification_service.preferences({"in_app": False, "webhook": True})) == ["webhook"]
