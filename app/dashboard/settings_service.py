from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.setting import Setting

PUBLISH_MODE_KEY = "instagram_publish_mode"

async def get_value(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    return row.value if row else default

async def set_value(session: AsyncSession, key: str, value: str, description: str | None = None) -> None:
    row = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if row:
        row.value = value
        if description is not None:
            row.description = description
    else:
        session.add(Setting(key=key, value=value, description=description))
    await session.commit()
