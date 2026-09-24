import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import get_sessionmaker
from app.routers import artifacts, automations, connect, create, notifications, providers, tasks, workspaces
from app.routing.router import routing_status
from app.services.providers import seed_examples, settle_descriptions
from app.services.runtime import ensure_runtimes
from app.services.workspaces import seed_from_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bevro.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_sessionmaker()()
    try:
        seed_examples(db)
        ensure_runtimes(db)
        settle_descriptions(db)
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
    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """A bug is still a sentence to the person reading it.

        The detail goes to the server log, where it belongs; the browser gets
        something it can act on instead of "Internal Server Error".
        """
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong at Bevro's end. The details are in the server log."},
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
