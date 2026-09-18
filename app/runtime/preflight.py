"""Production configuration preflight checks.

This module intentionally performs no network calls. It validates only the
settings that must exist before starting a long-running worker/dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.core.config import Settings


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_settings(settings: Settings) -> PreflightResult:
    errors: list[str] = []
    warnings: list[str] = []

    if settings.POSTGRES_SSL_MODE not in {"disable", "require"}:
        errors.append("POSTGRES_SSL_MODE must be 'disable' or 'require'")

    if settings.is_production:
        if settings.DEBUG:
            errors.append("DEBUG must be false in production")
        if not settings.POSTGRES_PASSWORD:
            errors.append("POSTGRES_PASSWORD is required in production")
        if settings.DB_ECHO:
            warnings.append("DB_ECHO is enabled in production")

    if not settings.TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN must be configured")
    if not settings.TELEGRAM_DASHBOARD_ADMIN_IDS:
        errors.append("TELEGRAM_DASHBOARD_ADMIN_IDS must contain at least one id")

    if not settings.AI_API_KEY:
        errors.append("AI_API_KEY must be configured")
    if not settings.INSTAGRAM_ACCESS_TOKEN:
        errors.append("INSTAGRAM_ACCESS_TOKEN must be configured")
    if not settings.INSTAGRAM_BUSINESS_ACCOUNT_ID:
        errors.append("INSTAGRAM_BUSINESS_ACCOUNT_ID must be configured")

    try:
        ZoneInfo(settings.SCHEDULER_TIMEZONE)
    except Exception:
        errors.append(f"Invalid SCHEDULER_TIMEZONE: {settings.SCHEDULER_TIMEZONE!r}")

    if settings.INSTAGRAM_MAX_PUBLISH_ATTEMPTS <= 0:
        errors.append("INSTAGRAM_MAX_PUBLISH_ATTEMPTS must be positive")
    if settings.INSTAGRAM_CONTAINER_POLL_MAX_ATTEMPTS <= 0:
        errors.append("INSTAGRAM_CONTAINER_POLL_MAX_ATTEMPTS must be positive")
    if settings.AI_CAPTION_CANDIDATES <= 0:
        errors.append("AI_CAPTION_CANDIDATES must be positive")

    base = settings.INSTAGRAM_MEDIA_BASE_URL.strip()
    if settings.MEDIA_STORAGE_BACKEND.lower() in ("s3", "r2"):
        s3_values = {
            "MEDIA_S3_ENDPOINT_URL": settings.MEDIA_S3_ENDPOINT_URL,
            "MEDIA_S3_ACCESS_KEY_ID": settings.MEDIA_S3_ACCESS_KEY_ID,
            "MEDIA_S3_SECRET_ACCESS_KEY": settings.MEDIA_S3_SECRET_ACCESS_KEY,
            "MEDIA_S3_BUCKET_NAME": settings.MEDIA_S3_BUCKET_NAME,
            "MEDIA_PUBLIC_BASE_URL": settings.MEDIA_PUBLIC_BASE_URL,
        }
        errors.extend(f"{name} must be configured for S3-compatible media storage" for name, value in s3_values.items() if not str(value).strip())
        if not base and settings.MEDIA_PUBLIC_BASE_URL.strip():
            base = settings.MEDIA_PUBLIC_BASE_URL.strip()
        if not base:
            errors.append("INSTAGRAM_MEDIA_BASE_URL must point to a public HTTPS media endpoint")
        elif not base.startswith("https://"):
            errors.append("INSTAGRAM_MEDIA_BASE_URL must use HTTPS")
    elif settings.MEDIA_STORAGE_BACKEND.lower() == "local":
        if not base:
            errors.append("INSTAGRAM_MEDIA_BASE_URL must point to a public HTTPS media endpoint")
        elif not base.startswith("https://"):
            errors.append("INSTAGRAM_MEDIA_BASE_URL must use HTTPS")
    else:
        errors.append("MEDIA_STORAGE_BACKEND must be either 's3' or 'local'")

    return PreflightResult(ok=not errors, errors=tuple(errors), warnings=tuple(warnings))


def assert_runtime_ready(settings: Settings) -> None:
    result = validate_settings(settings)
    if not result.ok:
        raise RuntimeError("Runtime preflight failed:\n- " + "\n- ".join(result.errors))
