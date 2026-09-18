"""
Application-wide logging configuration.

Call `configure_logging()` once at startup (done in app.main). Every module
should then do:

    import logging
    logger = logging.getLogger(__name__)

so log records carry the correct module name and respect the levels/handlers
configured here.
"""

import logging
import logging.config
import sys

from app.core.config import settings

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging() -> None:
    """Configure root logging for the whole process (console handler)."""
    log_level = settings.LOG_LEVEL.upper()

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": LOG_FORMAT,
                "datefmt": DATE_FORMAT,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "default",
                "level": log_level,
            },
        },
        "root": {
            "handlers": ["console"],
            "level": log_level,
        },
        "loggers": {
            # Quiet down noisy third-party loggers by default.
            "uvicorn.access": {"level": "WARNING", "propagate": True},
            "sqlalchemy.engine": {
                "level": "INFO" if settings.DB_ECHO else "WARNING",
                "propagate": True,
            },
        },
    }

    logging.config.dictConfig(logging_config)

    logger = logging.getLogger(__name__)
    logger.info(
        "Logging configured (env=%s, level=%s)", settings.APP_ENV, log_level
    )
