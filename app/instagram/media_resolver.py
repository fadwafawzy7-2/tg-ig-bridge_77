"""Resolve media.file_path into a public HTTPS URL for Meta."""
from __future__ import annotations

from urllib.parse import quote

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError


def resolve_media_url(*, file_path: str | None) -> str:
    if not file_path:
        raise ConfigurationError("Media has no file_path; it was not uploaded to persistent storage yet.")
    if file_path.startswith("https://"):
        return file_path
    if file_path.startswith("http://"):
        raise ConfigurationError("Instagram media URL must use HTTPS")
    settings = get_settings()
    base = settings.INSTAGRAM_MEDIA_BASE_URL.strip().rstrip("/")
    if not base:
        raise ConfigurationError("INSTAGRAM_MEDIA_BASE_URL is not configured")
    return f"{base}/{quote(file_path.lstrip('/'), safe='/') }"
