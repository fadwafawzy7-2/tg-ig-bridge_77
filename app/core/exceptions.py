"""
Application error hierarchy + centralized FastAPI exception handling.

Business/domain code should raise the exceptions defined here rather than
generic `Exception`, so failures map to predictable, structured HTTP
responses instead of leaking stack traces to clients.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for all application-raised errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str | None = None):
        self.message = message or "An unexpected error occurred."
        super().__init__(self.message)


class ConfigurationError(AppError):
    """Raised when required configuration is missing or invalid."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "configuration_error"


class DatabaseUnavailableError(AppError):
    """Raised when the database cannot be reached."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "database_unavailable"


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class ValidationError(AppError):
    """Raised for domain-level validation failures (not request parsing)."""

    status_code = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)
    error_code = "validation_error"


def _error_body(error_code: str, message: str) -> dict:
    return {"error": {"code": error_code, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach global exception handlers to the FastAPI app."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "Handled AppError: %s (%s) on %s %s",
            exc.error_code,
            exc.message,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.error_code, exc.message),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "An unexpected error occurred."),
        )
