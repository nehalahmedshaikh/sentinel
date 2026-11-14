"""FastAPI application factory and operational endpoints."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.staticfiles import StaticFiles

from sentinel.api.routes import datasets, incidents, operations, quality, reliability
from sentinel.observability import configure_logging, metrics_registry
from sentinel.persistence.database import create_database_engine
from sentinel.settings import Settings
from sentinel.storage.filesystem import FileStore

logger = logging.getLogger("sentinel.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        runtime = settings or Settings.from_environment()
        engine = create_database_engine(url=runtime.database_url)
        application.state.settings = runtime
        application.state.engine = engine
        application.state.storage = FileStore(Path(runtime.data_dir))
        application.state.metrics_registry = metrics_registry(engine)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Sentinel", version="0.5.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=(
            list(settings.cors_origins)
            if settings
            else [
                item.strip()
                for item in os.getenv("SENTINEL_CORS_ORIGINS", "").split(",")
                if item.strip()
            ]
        ),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "HX-Request"],
    )
    app.include_router(datasets.router)
    app.include_router(quality.router)
    app.include_router(reliability.router)
    app.include_router(incidents.router)
    app.include_router(operations.router)
    static_path = Path(__file__).parent.parent / "dashboard" / "static"
    app.mount("/static", StaticFiles(directory=static_path), name="static")

    from sentinel.dashboard.routes import router as dashboard_router

    app.include_router(dashboard_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(request: Request) -> dict[str, str]:
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            import redis

            redis.Redis.from_url(
                request.app.state.settings.redis_url, socket_connect_timeout=1
            ).ping()
        except Exception as exc:
            logger.warning("readiness check failed", extra={"error_class": type(exc).__name__})
            raise HTTPException(503, "Dependencies are not ready") from exc
        return {"status": "ready"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request) -> PlainTextResponse:
        return PlainTextResponse(
            generate_latest(request.app.state.metrics_registry), media_type=CONTENT_TYPE_LATEST
        )

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def versioned_openapi() -> dict[str, object]:
        return app.openapi()

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> PlainTextResponse:
        logger.exception("database request failed", extra={"path": request.url.path})
        return PlainTextResponse("Internal service error", status_code=500)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> PlainTextResponse:
        logger.exception("unhandled request failure", extra={"path": request.url.path})
        return PlainTextResponse("Internal service error", status_code=500)

    return app


app = create_app()
