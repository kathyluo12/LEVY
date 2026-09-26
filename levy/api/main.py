"""FastAPI application factory with lifespan bootstrap and CORS."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from levy.api.routes import router
from levy.core.db import get_repository, set_repository
from levy.core.events import get_event_bus
from levy.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repo = await get_repository(settings)
    # Ensure the event bus exists.
    get_event_bus()
    if settings.seed_on_start:
        from levy.seed import seed_repository

        await seed_repository(repo)
    app.state.repo = repo
    app.state.settings = settings
    yield
    await repo.close()
    set_repository(None)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LEVY — Long-Horizon Tariff Agent Desk", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": exc.errors(), "body": None})

    return app


app = create_app()
