"""Ephemeral-runner helpers for Telethon session materialization."""
from __future__ import annotations

import base64
from pathlib import Path

from app.core.config import Settings


def ensure_telethon_session(settings: Settings) -> Path:
    path = Path(settings.TELEGRAM_SESSION_NAME + ".session")
    if path.exists():
        return path
    encoded = settings.TELEGRAM_SESSION_B64.strip()
    if not encoded:
        raise RuntimeError(
            "Telethon session file is missing. Provide TELEGRAM_SESSION_B64 "
            "for ephemeral runners or create the session locally first."
        )
    try:
        path.write_bytes(base64.b64decode(encoded, validate=True))
    except Exception as exc:
        raise RuntimeError("TELEGRAM_SESSION_B64 is not valid base64") from exc
    return path
