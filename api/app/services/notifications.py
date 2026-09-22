"""Telling the person, once, and keeping the work out of it.

    a run that mattered → NotificationEvent → one delivery per channel

Three rules hold this together:

* A task that succeeded stays succeeded. Delivery lives in its own rows and
  can fail, be retried, or never be configured at all, and the work is
  untouched. Bevro never re-runs a provider because an email bounced.
* One occurrence means one event. The database enforces it, so two
  schedulers cannot both announce the same thing.
* A delivery is claimed before it is attempted, the same way work is, so two
  schedulers cannot both send the same message.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.delivery import EXTERNAL_CHANNELS, IN_APP, DeliveryResult, channels as build_channels, event_payload
from app.models import NotificationDelivery, NotificationEvent
from app.models._common import utcnow

log = logging.getLogger("bevro.notifications")

# A transient failure is worth another go, soon, then later, then later still.
# After that Bevro stops: the event stays in Bevro, where it always was.
RETRY_BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 1800)
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1

TITLE_MAX = 200
SUMMARY_MAX = 2000

# What a new automation does unless the person says otherwise: tell me here.
DEFAULT_PREFERENCES: dict[str, bool] = {"in_app": True, "email": False, "webhook": False, "on_finish": False}


def preferences(raw: dict[str, Any] | None) -> dict[str, bool]:
    """A stored preference, filled out with the defaults for anything missing."""
    merged = dict(DEFAULT_PREFERENCES)
    for key, value in (raw or {}).items():
        if key in merged:
            merged[key] = bool(value)
    return merged


def wanted_channels(prefs: dict[str, bool]) -> list[str]:
    """Which channels this automation asked for, in the order they are tried."""
    chosen = [IN_APP] if prefs.get("in_app", True) else []
    return chosen + [name for name in EXTERNAL_CHANNELS if prefs.get(name)]


# --------------------------------------------------------------------------- raising one

def raise_event(
    db: Session,
    *,
    kind: str,
    title: str,
    summary: str | None = None,
    reason: str | None = None,
    automation: Any = None,
    run: Any = None,
    task: Any = None,
    prefs: dict[str, Any] | None = None,
    settings: Any = None,
) -> NotificationEvent | None:
    """Record that something is worth telling the person, and queue the telling.

    Returns the event, or None when this occurrence has already been
    announced. Caller commits.
    """
    if run is not None and existing_for_run(db, run.id) is not None:
        return None
    event = NotificationEvent(
        kind=kind,
        title=(title or "Bevro")[:TITLE_MAX],
        summary=(summary or None) and summary[:SUMMARY_MAX],
        reason=reason,
        automation_id=getattr(automation, "id", None),
        automation_run_id=getattr(run, "id", None),
        task_id=getattr(task, "id", None),
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        # Another scheduler announced this occurrence a moment ago.
        db.rollback()
        return None
    queue(db, event, prefs=prefs, settings=settings)
    log.info("notification %s raised (%s)", event.id, kind)
    return event


def existing_for_run(db: Session, run_id: uuid.UUID) -> NotificationEvent | None:
    return db.scalars(select(NotificationEvent).where(NotificationEvent.automation_run_id == run_id)).first()


def queue(db: Session, event: NotificationEvent, *, prefs: dict[str, Any] | None = None, settings: Any = None) -> list[NotificationDelivery]:
    """Line up one delivery per channel the person asked for."""
    settings = settings or get_settings()
    available = build_channels(settings)
    moment = utcnow()
    made: list[NotificationDelivery] = []
    # An external delivery is queued with no time on it: it goes with the next
    # turn of the sender, whenever that is. Only a retry is given a moment.
    for name in wanted_channels(preferences(prefs)):
        channel = available.get(name)
        if channel is None:
            continue
        problem = channel.validate_configuration()
        if problem:
            # Asked for, but not set up here. Say so plainly rather than
            # pretending it went, and never try to send it.
            made.append(_add(db, event, name, status="failed", error=problem))
            continue
        if not channel.external:
            # In Bevro: the event itself is the delivery.
            made.append(_add(db, event, name, status="sent", delivered_at=moment))
            continue
        made.append(_add(db, event, name, status="pending", destination=channel.default_destination()))
    db.flush()
    return made


def _add(db: Session, event: NotificationEvent, channel: str, *, status: str, destination: str | None = None, next_attempt_at: datetime | None = None, delivered_at: datetime | None = None, error: str | None = None) -> NotificationDelivery:
    delivery = NotificationDelivery(
        event_id=event.id,
        channel=channel,
        destination=destination,
        status=status,
        next_attempt_at=next_attempt_at,
        delivered_at=delivered_at,
        last_error=error,
    )
    db.add(delivery)
    return delivery


# --------------------------------------------------------------------------- sending

def claim_deliveries(db: Session, *, now: datetime | None = None, limit: int = 20) -> list[NotificationDelivery]:
    """Take the deliveries that are due to be attempted. One sender only."""
    moment = now or utcnow()
    rows = db.execute(
        sql_text(
            """
            SELECT id FROM notification_deliveries
            WHERE status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
            ORDER BY next_attempt_at NULLS FIRST
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"now": moment, "limit": limit},
    ).all()
    claimed: list[NotificationDelivery] = []
    for (delivery_id,) in rows:
        delivery = db.get(NotificationDelivery, delivery_id)
        if delivery is None or delivery.status != "pending":
            continue
        delivery.status = "sending"
        delivery.attempts += 1
        db.flush()
        claimed.append(delivery)
    db.commit()
    return claimed


def attempt(db: Session, delivery: NotificationDelivery, *, settings: Any = None, now: datetime | None = None) -> bool:
    """Try one delivery and record how it went. Never raises. Commits."""
    settings = settings or get_settings()
    moment = now or utcnow()
    channel = build_channels(settings).get(delivery.channel)
    if channel is None:
        _give_up(db, delivery, f"Bevro no longer knows how to deliver by {delivery.channel}.")
        return False
    payload = event_payload(delivery.event, app_url=settings.app_url)
    try:
        result = channel.deliver(payload, delivery.destination)
    except Exception as exc:  # noqa: BLE001 - a channel must never break the loop
        log.exception("delivery %s raised", delivery.id)
        result = DeliveryResult.temporary_failure(f"Delivery failed unexpectedly ({type(exc).__name__}).")

    if result.ok:
        delivery.status = "sent"
        delivery.delivered_at = moment
        delivery.last_error = None
        delivery.next_attempt_at = None
        db.commit()
        log.info("notification %s delivered by %s", delivery.event_id, delivery.channel)
        return True

    if result.permanent:
        _give_up(db, delivery, result.detail)
        return False
    if delivery.attempts >= MAX_ATTEMPTS:
        _give_up(db, delivery, f"{result.detail} Bevro stopped trying after {delivery.attempts} attempts.")
        return False
    wait = RETRY_BACKOFF_SECONDS[min(delivery.attempts, len(RETRY_BACKOFF_SECONDS)) - 1]
    delivery.status = "pending"
    delivery.last_error = result.detail
    delivery.next_attempt_at = moment + timedelta(seconds=wait)
    db.commit()
    log.info("notification %s: %s delivery will be tried again in %ss (%s)", delivery.event_id, delivery.channel, wait, result.detail)
    return False


def _give_up(db: Session, delivery: NotificationDelivery, detail: str) -> None:
    delivery.status = "failed"
    delivery.last_error = detail
    delivery.next_attempt_at = None
    db.commit()
    log.info("notification %s: %s delivery failed (%s)", delivery.event_id, delivery.channel, detail)


def deliver_pending(db: Session, *, now: datetime | None = None, limit: int = 20, settings: Any = None) -> int:
    """Send whatever is waiting. Called every turn by the scheduler."""
    sent = 0
    for delivery in claim_deliveries(db, now=now, limit=limit):
        if attempt(db, delivery, settings=settings, now=now):
            sent += 1
    return sent


def retry_failed(db: Session, event: NotificationEvent, *, now: datetime | None = None) -> int:
    """Try the external channels again, at the person's request. Caller commits."""
    moment = now or utcnow()
    tried = 0
    for delivery in event.deliveries:
        if delivery.status != "failed":
            continue
        delivery.status = "pending"
        delivery.attempts = 0
        delivery.next_attempt_at = moment
        delivery.last_error = None
        tried += 1
    db.flush()
    return tried


# --------------------------------------------------------------------------- reading

def unread_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(NotificationEvent).where(NotificationEvent.read_at.is_(None))) or 0)


def listing(db: Session, *, unread_only: bool = False, limit: int = 50) -> list[NotificationEvent]:
    query = select(NotificationEvent).order_by(NotificationEvent.created_at.desc()).limit(limit)
    if unread_only:
        query = query.where(NotificationEvent.read_at.is_(None))
    return list(db.scalars(query))


def get(db: Session, event_id: uuid.UUID) -> NotificationEvent | None:
    return db.get(NotificationEvent, event_id)


def mark_read(db: Session, event: NotificationEvent, *, now: datetime | None = None) -> NotificationEvent:
    """Opening it, or saying so, clears it. Nothing else does. Caller commits."""
    if event.read_at is None:
        event.read_at = now or utcnow()
        db.flush()
    return event


def mark_all_read(db: Session, *, now: datetime | None = None) -> int:
    """Only ever from an explicit "Mark all read": looking at the list is not reading."""
    moment = now or utcnow()
    unread = list(db.scalars(select(NotificationEvent).where(NotificationEvent.read_at.is_(None))))
    for event in unread:
        event.read_at = moment
    db.flush()
    return len(unread)


def delivery_state(event: NotificationEvent) -> str:
    """The event's delivery in one word, for the browser."""
    external = [d for d in event.deliveries if d.channel != IN_APP]
    if not external:
        return "in_app"
    if any(d.status in ("pending", "sending") for d in external):
        return "sending"
    if all(d.status == "sent" for d in external):
        return "delivered"
    if any(d.status == "sent" for d in external):
        return "partly_delivered"
    return "delivery_failed"


def delivery_problem(event: NotificationEvent) -> str | None:
    """Why external delivery has not happened, in words. Never a server banner.

    A message still waiting says so too: a try that failed is worth knowing
    about even while Bevro intends to have another go.
    """
    for delivery in event.deliveries:
        if delivery.status == "failed" and delivery.last_error:
            return delivery.last_error
    for delivery in event.deliveries:
        if delivery.status in ("pending", "sending") and delivery.last_error:
            return f"{delivery.last_error} Bevro will try again."
    return None


def channels_used(event: NotificationEvent) -> Iterable[str]:
    return sorted({d.channel for d in event.deliveries})
