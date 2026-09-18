"""
Health-check endpoints.

Two separate checks, following standard container-orchestration practice:

- /health/live  -> "is the process running at all?" (never touches the DB;
                    used by Docker/Kubernetes to decide whether to restart)
- /health/ready -> "can the process actually serve traffic?" (checks DB
                    connectivity; used to gate traffic routing)
"""

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.session import check_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


@router.get("/ready")
async def readiness() -> JSONResponse:
    db_ok = await check_db_connection()

    body = {
        "status": "ok" if db_ok else "unavailable",
        "checks": {"database": "ok" if db_ok else "unreachable"},
    }
    status_code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=body)
