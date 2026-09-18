"""Persistent media upload for ephemeral GitHub Actions runners.

Works with any S3-compatible object storage provider — Supabase Storage,
Cloudflare R2, AWS S3, Backblaze B2, etc. — driven entirely by the generic
MEDIA_S3_* / MEDIA_PUBLIC_BASE_URL settings (see app/core/config.py).
Path-style addressing is forced because some providers (Supabase Storage)
require it; it also works fine against R2 and AWS S3.
"""
from __future__ import annotations

from pathlib import Path
import mimetypes

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError


def _settings():
    settings = get_settings()
    required = {
        "MEDIA_S3_ENDPOINT_URL": settings.MEDIA_S3_ENDPOINT_URL,
        "MEDIA_S3_ACCESS_KEY_ID": settings.MEDIA_S3_ACCESS_KEY_ID,
        "MEDIA_S3_SECRET_ACCESS_KEY": settings.MEDIA_S3_SECRET_ACCESS_KEY,
        "MEDIA_S3_BUCKET_NAME": settings.MEDIA_S3_BUCKET_NAME,
        "MEDIA_PUBLIC_BASE_URL": settings.MEDIA_PUBLIC_BASE_URL,
    }
    missing = [k for k, v in required.items() if not str(v).strip()]
    if missing:
        raise ConfigurationError("Missing media storage settings: " + ", ".join(missing))
    return settings


def _client(settings):
    from boto3.session import Session
    from botocore.config import Config

    session = Session(
        aws_access_key_id=settings.MEDIA_S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.MEDIA_S3_SECRET_ACCESS_KEY,
        region_name=settings.MEDIA_S3_REGION or "auto",
    )
    return session.client(
        "s3",
        endpoint_url=settings.MEDIA_S3_ENDPOINT_URL,
        config=Config(s3={"addressing_style": "path"}),
    )


def upload_file(path: str, object_key: str) -> str:
    """Upload one local file to object storage and return its public HTTPS URL."""
    settings = _settings()
    client = _client(settings)
    local = Path(path)
    if not local.is_file():
        raise FileNotFoundError(path)
    content_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
    client.upload_file(
        str(local),
        settings.MEDIA_S3_BUCKET_NAME,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return settings.MEDIA_PUBLIC_BASE_URL.rstrip("/") + "/" + object_key.lstrip("/")


def delete_file(object_key: str) -> None:
    """Delete one object from storage. A no-op (no error) if it's already gone."""
    settings = _settings()
    client = _client(settings)
    client.delete_object(Bucket=settings.MEDIA_S3_BUCKET_NAME, Key=object_key)


def object_key_from_public_url(file_path: str) -> str | None:
    """Reverse of upload_file()'s URL construction: given a stored
    `media.file_path` value, return the object key, or None if
    `file_path` isn't one of our public storage URLs (e.g. a local
    relative path, used when MEDIA_STORAGE_BACKEND=local).

    Deliberately does not call `_settings()` (which requires every S3
    setting to be present) — this must also work when object storage
    isn't configured at all, simply returning None in that case.
    """
    base = (get_settings().MEDIA_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None
    prefix = base + "/"
    if file_path.startswith(prefix):
        return file_path[len(prefix):]
    return None
