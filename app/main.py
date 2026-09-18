"""FastAPI application entrypoint, health endpoints, and public media serving."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.api.channels import router as channels_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import engine
from app.media.storage import guess_content_type, safe_relative_path, storage_root

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting %s (env=%s, debug=%s)", settings.APP_NAME, settings.APP_ENV, settings.DEBUG)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        debug=settings.DEBUG,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(channels_router)

    @app.get("/media/{file_path:path}")
    async def media(file_path: str):
        root = storage_root(settings.MEDIA_STORAGE_DIR)
        try:
            resolved = safe_relative_path(root, file_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid media path") from exc
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="media not found")
        return FileResponse(resolved, media_type=guess_content_type(resolved))

    return app


app = create_app()
