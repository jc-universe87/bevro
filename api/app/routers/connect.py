"""Connect: one input in, a draft out, a provider after confirmation.

Thin on purpose: classification, discovery, testing and confirmation live in
app.services.connect and app.connect. This file maps them to HTTP.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ConnectDraft
from app.schemas.connect import BridgeStatusOut, ConfirmIn, DiscoverIn, DraftOut, TestIn
from app.schemas.providers import ProviderOut
from app.schemas.serialise import provider_out
from app.services import connect as connect_service
from app.services.secrets import SecretStore

router = APIRouter(prefix="/connect", tags=["connect"])

_STATE_OUT = {"pending": "looking"}


def _out(row: ConnectDraft, db: Session | None = None) -> DraftOut:
    return DraftOut(
        id=row.id,
        state=_STATE_OUT.get(row.state, row.state),
        target_kind=row.target_kind,
        target_label=connect_service.target_label(row),
        draft=connect_service.draft_public(row, db),
        error=row.error,
        test=row.test,
        provider_id=row.provider_id,
        created_at=row.created_at,
    )


def _load(db: Session, draft_id: uuid.UUID) -> ConnectDraft:
    row = connect_service.get_draft(db, draft_id)
    if row is None:
        raise HTTPException(404, "That connection attempt wasn't found.")
    return row


@router.post("/discover", response_model=DraftOut, status_code=201)
def discover(body: DiscoverIn, db: Session = Depends(get_db)) -> DraftOut:
    try:
        row = connect_service.start_discovery(db, body.target, body.secrets)
    except connect_service.DraftError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return _out(row, db)


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: uuid.UUID, db: Session = Depends(get_db)) -> DraftOut:
    return _out(_load(db, draft_id), db)


@router.post("/drafts/{draft_id}/test", response_model=DraftOut)
def test_draft(draft_id: uuid.UUID, body: TestIn, db: Session = Depends(get_db)) -> DraftOut:
    row = _load(db, draft_id)
    try:
        row = connect_service.test_draft(db, row, body.secrets)
    except connect_service.DraftError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return _out(row, db)


@router.post("/drafts/{draft_id}/bridge", response_model=BridgeStatusOut, status_code=201)
def make_connectable(draft_id: uuid.UUID, db: Session = Depends(get_db)) -> BridgeStatusOut:
    """"Make it connectable": have a coding agent build Bevro's own small
    connection for a project that has no way in of its own."""
    from app.services import bridges as bridge_service

    row = _load(db, draft_id)
    try:
        provider = bridge_service.start_build(db, row)
    except bridge_service.BridgeError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return BridgeStatusOut(**bridge_service.status(db, provider))


@router.get("/bridges/{provider_id}", response_model=BridgeStatusOut)
def bridge_status(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> BridgeStatusOut:
    from app.services import bridges as bridge_service
    from app.services import providers as provider_service

    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "That agent wasn't found.")
    return BridgeStatusOut(**bridge_service.status(db, provider))


@router.post("/drafts/{draft_id}/confirm", response_model=ProviderOut, status_code=201)
def confirm(draft_id: uuid.UUID, body: ConfirmIn, db: Session = Depends(get_db)) -> ProviderOut:
    row = _load(db, draft_id)
    try:
        provider = connect_service.confirm_draft(
            db,
            row,
            name=body.name,
            description=body.description,
            capability_summary=body.capability_summary,
            secrets=body.secrets,
            app_url=(body.app_url or "").strip() or None,
            enabled=body.enabled,
            runtime_id=body.runtime_id, scope=body.scope,
        )
    except connect_service.DraftError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    try:
        names = SecretStore().names(db, provider.id)
    except RuntimeError:
        names = []
    return provider_out(provider, names)
