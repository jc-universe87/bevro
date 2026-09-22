import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import get_sessionmaker
from app.routers import artifacts, automations, connect, create, notifications, providers, tasks, workspaces
from app.routing.router import routing_status
from app.services.providers import seed_examples
from app.services.runtime import ensure_runtimes
from app.services.workspaces import seed_from_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_sessionmaker()()
    try:
        seed_examples(db)
        ensure_runtimes(db)
        seed_from_config(db)
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Bevro API", version=settings.app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api = APIRouter(prefix="/api")

    @api.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/meta", tags=["meta"])
    def meta() -> dict[str, Any]:
        return {"name": "Bevro", "version": settings.app_version, "tagline": "Agents that get things done.", "routing": routing_status()}

    api.include_router(tasks.router)
    api.include_router(artifacts.router)
    api.include_router(providers.router)
    api.include_router(automations.router)
    api.include_router(notifications.router)
    api.include_router(connect.router)
    api.include_router(create.router)
    api.include_router(workspaces.router)
    app.include_router(api)
    return app


app = create_app()
