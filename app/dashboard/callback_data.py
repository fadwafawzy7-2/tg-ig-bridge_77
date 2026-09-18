"""Compact, deterministic callback-data encoding for dashboard buttons."""
from __future__ import annotations

PREFIX = "d9"


def encode(action: str, value: str | int | None = None) -> str:
    return f"{PREFIX}:{action}" + (f":{value}" if value is not None else "")


def decode(data: str | None) -> tuple[str, str | None] | None:
    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) < 2 or parts[0] != PREFIX or not parts[1]:
        return None
    return parts[1], parts[2] if len(parts) == 3 else None
