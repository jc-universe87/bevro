"""Being told: the unread count, the list, and marking things read."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.delivery import availability
from app.models import NotificationEvent
from app.schemas.notifications import ChannelOut, NotificationList, NotificationOut
from app.services import notifications as notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(event: NotificationEvent) -> NotificationOut:
    return NotificationOut(
        id=event.id,
        kind=event.kind,
        title=event.title,
        summary=event.summary,
        reason=event.reason,
        read=event.read_at is not None,
        task_id=event.task_id,
        automation_id=event.automation_id,
        delivery=notification_service.delivery_state(event),
        channels=list(notification_service.channels_used(event)),
        delivery_problem=notification_service.delivery_problem(event),
        created_at=event.created_at,
    )


def _load(db: Session, event_id: uuid.UUID) -> NotificationEvent:
    event = notification_service.get(db, event_id)
    if event is None:
        raise HTTPException(404, "That notification wasn't found.")
    return event


@router.get("", response_model=NotificationList)
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> NotificationList:
    """Reading the list does not mark anything read."""
    events = notification_service.listing(db, unread_only=unread_only, limit=limit)
    return NotificationList(unread=notification_service.unread_count(db), items=[_out(e) for e in events])


@router.get("/channels", response_model=list[ChannelOut])
def list_channels() -> list[ChannelOut]:
    """What this installation can do. Never host names or passwords."""
    return [ChannelOut(**c) for c in availability()]


@router.post("/{event_id}/read", response_model=NotificationOut)
def mark_read(event_id: uuid.UUID, db: Session = Depends(get_db)) -> NotificationOut:
    event = _load(db, event_id)
    notification_service.mark_read(db, event)
    db.commit()
    return _out(event)


@router.post("/read-all", response_model=NotificationList)
def mark_all_read(db: Session = Depends(get_db)) -> NotificationList:
    notification_service.mark_all_read(db)
    db.commit()
    return list_notifications(unread_only=False, limit=50, db=db)


@router.post("/{event_id}/retry", response_model=NotificationOut)
def retry_delivery(event_id: uuid.UUID, db: Session = Depends(get_db)) -> NotificationOut:
    """Send it again. The work itself is not touched and never re-run."""
    event = _load(db, event_id)
    if notification_service.retry_failed(db, event) == 0:
        raise HTTPException(409, "There's nothing waiting to be sent again.")
    db.commit()
    notification_service.deliver_pending(db)
    db.refresh(event)
    return _out(event)


@router.delete("/{event_id}", status_code=204)
def remove(event_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    db.delete(_load(db, event_id))
    db.commit()
