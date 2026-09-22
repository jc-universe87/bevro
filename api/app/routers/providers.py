from __future__ import annotations

import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connect.targets import TargetError, split_command
from app.db import get_db
from app.models import Provider, ProviderRun
from app.schemas.providers import HealthOut, ProviderConnect, ProviderDetails, ProviderOut, ProviderUpdate, SecretIn
from app.schemas.serialise import provider_details, provider_out
from app.services import providers as provider_service
from app.services.secrets import SecretStore

router = APIRouter(prefix="/providers", tags=["providers"])

# Advanced-setup method -> adapter transport.
_METHOD_TO_ADAPTER = {"api": "http", "local": "http", "mcp": "mcp", "command": "command"}
_TEMPLATE_RE = re.compile(r"^\s*(GET|POST|PUT)?\s*(/\S*)\s*(\{.*\})?\s*$", re.S)


def _text(details: dict[str, Any], key: str) -> str:
    value = details.get(key)
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _advanced_config(body: ProviderConnect, kind: str) -> dict[str, Any]:
    """Turn the handful of Advanced-setup fields into an adapter config. Secrets never enter it."""
    d = body.details
    config: dict[str, Any] = {}
    if kind == "http":
        base = _text(d, "base_url")
        if not base:
            raise HTTPException(422, "An address is needed for this connection.")
        if not base.startswith(("http://", "https://")):
            raise HTTPException(422, "The address must start with http:// or https://.")
        config["base_url"] = base.rstrip("/")
        template = _text(d, "request_template")
        if template:
            m = _TEMPLATE_RE.match(template)
            if not m:
                raise HTTPException(422, 'The request template should look like: POST /ask {"query": "{request}"}')
            method, path, raw_body = m.group(1) or "POST", m.group(2), m.group(3)
            try:
                body_json = json.loads(raw_body) if raw_body else {"request": "{request}"}
            except ValueError:
                raise HTTPException(422, "The request template's JSON part could not be read.") from None
            config["invoke"] = {"method": method, "path": path, "body": body_json}
            field = _text(d, "response_field")
            if field:
                config["response"] = {"text": field, "summary": field}
        elif _text(d, "invoke_path"):
            config["invoke_path"] = _text(d, "invoke_path")
        if body.secrets.get("api_key"):
            config["auth"] = {"type": "bearer", "secret": "api_key"}
    elif kind == "mcp":
        server_url, command = _text(d, "server_url"), _text(d, "command")
        if not server_url and not command:
            raise HTTPException(422, "A server address or a command is needed for an MCP connection.")
        if server_url:
            config["server_url"] = server_url
            if body.secrets.get("api_key"):
                config["auth"] = {"type": "bearer", "secret": "api_key"}
        else:
            config["argv"] = _argv(command)
            if _text(d, "working_directory"):
                config["cwd"] = _text(d, "working_directory")
        tool = _text(d, "tool")
        if tool:
            name, _, argument = tool.partition(":")
            config["tool"] = {"name": name.strip(), "argument": (argument.strip() or "query")}
        else:
            config["tool"] = {}
    elif kind == "command":
        command = _text(d, "command")
        if not command:
            raise HTTPException(422, "A command is needed.")
        config["argv"] = _argv(command)
        if _text(d, "working_directory"):
            config["cwd"] = _text(d, "working_directory")
        flag = _text(d, "input_flag")
        if flag == "stdin":
            config["input"] = {"mode": "stdin"}
        elif flag == "none":
            config["input"] = {"mode": "none"}
        elif flag:
            if not flag.startswith("-"):
                raise HTTPException(422, "The input option should start with a dash, e.g. --topic.")
            config["input"] = {"mode": "flag", "flag": flag}
        else:
            config["input"] = {"mode": "argument"}
        config["output"] = {"mode": "stdout"}
        config["env"] = {}
        secret_env = _text(d, "secret_env")
        config["secret_env"] = [secret_env] if secret_env and secret_env in body.secrets else []
        config["timeout_seconds"] = 1800
    return config


def _argv(command: str) -> list[str]:
    try:
        return split_command(command)
    except TargetError as exc:
        raise HTTPException(422, str(exc)) from None


def _secret_store_or_none() -> SecretStore | None:
    try:
        return SecretStore()
    except RuntimeError:
        return None


def _out(db: Session, provider: Provider) -> ProviderOut:
    store = _secret_store_or_none()
    names = store.names(db, provider.id) if store else []
    return provider_out(provider, names, db)


@router.get("", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db)) -> list[ProviderOut]:
    return [_out(db, p) for p in provider_service.list_providers(db)]


@router.get("/{provider_id}", response_model=ProviderOut)
def get_provider(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> ProviderOut:
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    return _out(db, provider)


@router.post("", response_model=ProviderOut, status_code=201)
def connect_provider(body: ProviderConnect, db: Session = Depends(get_db)) -> ProviderOut:
    """Advanced setup. The normal Connect flow goes through /api/connect instead."""
    kind = _METHOD_TO_ADAPTER[body.method]
    config = _advanced_config(body, kind)

    store = _secret_store_or_none()
    if body.secrets and store is None:
        raise HTTPException(503, "Secrets cannot be stored until the server has a secret key.")

    provider = provider_service.register_provider(
        db,
        {
            "name": body.name,
            "description": body.description,
            "capabilities": body.capabilities,
            "adapter": {"kind": kind, "method": body.method, "config": config},
            "app_url": body.app_url,
            "icon": {"kind": "letter", "text": body.name[:1].upper()},
            "origin": "connected",
        },
    )
    if store is not None:
        for name, value in body.secrets.items():
            if value:
                store.put(db, provider.id, name, value)
    db.commit()
    db.refresh(provider)
    return _out(db, provider)


@router.patch("/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: uuid.UUID, body: ProviderUpdate, db: Session = Depends(get_db)) -> ProviderOut:
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(provider, field, value)
    db.commit()
    db.refresh(provider)
    return _out(db, provider)


@router.delete("/{provider_id}", status_code=204)
def remove_provider(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    if provider.origin == "example":
        raise HTTPException(409, "Built-in agents cannot be removed.")
    has_runs = db.scalar(select(ProviderRun.id).where(ProviderRun.provider_id == provider.id).limit(1))
    if has_runs is not None:
        provider.enabled = False
        db.commit()
        return None
    db.delete(provider)
    db.commit()
    return None


_SECRET_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")


@router.put("/{provider_id}/secrets/{name}", response_model=ProviderOut)
def put_secret(provider_id: uuid.UUID, name: str, body: SecretIn, db: Session = Depends(get_db)) -> ProviderOut:
    """Add or replace one credential. Stored encrypted; the value is never returned."""
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    if not _SECRET_NAME_RE.match(name):
        raise HTTPException(422, "That isn't a valid credential name.")
    store = _secret_store_or_none()
    if store is None:
        raise HTTPException(503, "Secrets cannot be stored until the server has a secret key.")
    store.put(db, provider.id, name, body.value.strip())
    db.commit()
    db.refresh(provider)
    return _out(db, provider)


@router.delete("/{provider_id}/secrets/{name}", response_model=ProviderOut)
def delete_secret(provider_id: uuid.UUID, name: str, db: Session = Depends(get_db)) -> ProviderOut:
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    store = _secret_store_or_none()
    if store is not None and store.delete(db, provider.id, name):
        db.commit()
    db.refresh(provider)
    return _out(db, provider)


@router.get("/{provider_id}/details", response_model=ProviderDetails)
def get_details(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> ProviderDetails:
    """Advanced details: which runtime mechanisms were found and which is active. Names of kinds only."""
    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    return provider_details(provider)


@router.post("/{provider_id}/reconnect", response_model=ProviderOut)
def reconnect(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> ProviderOut:
    """Look at the original target again and refresh how the provider runs. Keeps name, secrets and history."""
    from app.services import connect as connect_service

    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    try:
        connect_service.reconnect_provider(db, provider)
    except connect_service.DraftError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return _out(db, provider)


@router.post("/{provider_id}/rebuild", response_model=HealthOut)
def rebuild_connection(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> HealthOut:
    """Look at the project again: prefer a connection it now has of its own,
    otherwise build Bevro's own one afresh."""
    from app.services import bridges as bridge_service

    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    try:
        note = bridge_service.rebuild(db, provider)
    except bridge_service.BridgeError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return HealthOut(ok=True, detail=note)


@router.post("/{provider_id}/check", response_model=HealthOut)
def check_provider(provider_id: uuid.UUID, db: Session = Depends(get_db)) -> HealthOut:
    """Test every way Bevro has of reaching this agent, and say so in plain words.

    A way that answers is restored, so an agent that was down comes back
    without being reconnected.
    """
    from app.services import runtime as runtime_service

    provider = provider_service.get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(404, "Agent not found.")
    store = _secret_store_or_none()
    secrets = store.resolve(db, provider.id) if store else {}
    # Whatever this process can reach is tested now; a folder or a command on
    # the host belongs to the worker, which reports on its own.
    outcomes, deferred = runtime_service.check_all_runtimes(provider, secrets, execution="inline")
    db.commit()
    worker_note = provider_service.availability_of(provider) if deferred else None
    worker_ok = bool(worker_note and worker_note["state"] == "available")

    if not outcomes and not deferred:
        result = provider_service.check_health(provider, secrets)
        db.commit()
        return HealthOut(ok=result.ok, detail=result.detail)
    if not outcomes:
        detail = worker_note["note"] or ((provider.availability or {}).get("detail") if worker_ok else None)
        return HealthOut(ok=worker_ok, detail=detail if worker_ok else (worker_note["note"] or "No worker has reported on this yet."))

    working = [rt for rt, result in outcomes if result.ok]
    total = len(outcomes) + len(deferred)
    reachable = len(working) + (len(deferred) if worker_ok else 0)
    if not reachable:
        return HealthOut(ok=False, detail=next((r.detail for _rt, r in outcomes if r.detail), None) or "Not reachable.")
    name = working[0].display_name if working else deferred[0].display_name
    if total == 1:
        return HealthOut(ok=True, detail=name)
    missing = total - reachable
    return HealthOut(ok=True, detail=f"{name}." + (f" {missing} other way(s) couldn't be reached." if missing else f" All {total} ways work."))
