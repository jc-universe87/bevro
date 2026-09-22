from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.create.spec import AgentSpec
from app.db import get_db
from app.schemas.providers import CreateActivateIn, CreateBuildIn, CreateEditIn, CreatePreviewIn, CreatePreviewOut, CreateStatusOut, ProviderOut
from app.schemas.serialise import provider_out
from app.services import agents as agent_service
from app.services import create as create_service
from app.services import providers as provider_service

router = APIRouter(prefix="/create", tags=["create"])


def _preview_out(spec: AgentSpec, db: Session) -> CreatePreviewOut:
    view = spec.preview()
    return CreatePreviewOut(
        name=view["name"],
        description=view["description"],
        can=view["can"],
        needs=view["needs"],
        produces=view["produces"],
        schedule=view["schedule"],
        can_build=agent_service.can_build(db),
        spec=spec.model_dump(mode="json"),
        capabilities=spec.capability_dicts(),
        permissions=view["needs"],
    )


@router.post("/preview", response_model=CreatePreviewOut)
def preview(body: CreatePreviewIn, db: Session = Depends(get_db)) -> CreatePreviewOut:
    """Turn one sentence into an agreed description. Nothing is built yet."""
    return _preview_out(create_service.preview_spec(body.description), db)


@router.post("/build", response_model=CreateStatusOut, status_code=201)
def build(body: CreateBuildIn, db: Session = Depends(get_db)) -> CreateStatusOut:
    """Create the agent that was previewed. The work happens on the worker."""
    try:
        spec = AgentSpec.model_validate(body.spec) if body.spec else create_service.preview_spec(body.description)
    except ValueError:
        raise HTTPException(422, "That description couldn't be used.") from None
    try:
        provider, _build = agent_service.start_build(db, spec)
    except agent_service.CreateError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return CreateStatusOut(**agent_service.status(db, provider))


@router.get("/builds/{provider_id}", response_model=CreateStatusOut)
def build_status(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> CreateStatusOut:
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "That agent wasn't found.")
    return CreateStatusOut(**agent_service.status(db, provider))


@router.post("/builds/{provider_id}/rebuild", response_model=CreateStatusOut, status_code=201)
def rebuild(provider_id: uuid.UUID, body: CreateEditIn | None = None, db: Session = Depends(get_db)) -> CreateStatusOut:
    """Build a new version. What works now keeps working until the new one passes."""
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "That agent wasn't found.")
    spec = None
    if body is not None and body.description.strip():
        spec = create_service.preview_spec(body.description)
    try:
        agent_service.rebuild(db, provider, spec)
    except agent_service.CreateError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return CreateStatusOut(**agent_service.status(db, provider))


@router.post("/activate", response_model=ProviderOut, status_code=201)
def activate(body: CreateActivateIn, db: Session = Depends(get_db)) -> ProviderOut:
    """Older path: record an agent's description without building it."""
    spec = body.model_dump(exclude={"source_description"})
    provider = create_service.activate(db, spec, body.source_description)
    return provider_out(provider)
