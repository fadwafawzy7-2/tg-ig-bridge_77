"""Pure Telegram dashboard authorization helpers."""
from __future__ import annotations


def parse_admin_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                values.add(int(part))
            except ValueError:
                continue
    return frozenset(values)


def is_admin(user_id: int | None, admin_ids: frozenset[int]) -> bool:
    return user_id is not None and user_id in admin_ids
