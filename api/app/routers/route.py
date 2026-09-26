"""Ask which app or agent fits a request, and how to use it, before anything runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Provider
from app.routing.resolve import resolve
from app.schemas.route import RouteAnswer, RouteRequest

router = APIRouter(prefix="/route", tags=["routing"])


@router.post("", response_model=RouteAnswer)
def route(body: RouteRequest, db: Session = Depends(get_db)) -> RouteAnswer:
    """Nothing is created: no task, no run. Home acts on the answer."""
    text = body.request.strip()
    if not text:
        raise HTTPException(422, "Say what you want to get done.")
    chosen = None
    if body.provider_id is not None:
        chosen = db.get(Provider, body.provider_id)
        if chosen is None or not chosen.enabled:
            raise HTTPException(404, "That app or agent isn't in Bevro any more.")
    return RouteAnswer.model_validate(resolve(db, text, chosen=chosen).public())
