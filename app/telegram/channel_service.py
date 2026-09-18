"""
Dynamic Telegram channel management.

Channels are never hard-deleted (see `app/models/channel.py`): "removing"
a channel means `disable_channel()`, which sets `status = DISABLED`. This
keeps every `source_messages` row that references it valid forever.

`add_channel()` is idempotent: adding a channel that's already tracked
(active or disabled) updates its snapshot fields and re-activates it if it
was disabled, rather than creating a duplicate row — safe to call
repeatedly, e.g. from a "sync channel list" admin action.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.channel import Channel
from app.models.enums import ChannelStatus
from app.models.scan_state import ScanState


async def get_channel(session: AsyncSession, channel_id: int) -> Channel:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise NotFoundError(f"Channel {channel_id} not found.")
    return channel


async def get_channel_by_telegram_id(
    session: AsyncSession, telegram_channel_id: int
) -> Channel | None:
    result = await session.execute(
        select(Channel).where(Channel.telegram_channel_id == telegram_channel_id)
    )
    return result.scalar_one_or_none()


async def list_channels(
    session: AsyncSession, status: ChannelStatus | None = None
) -> list[Channel]:
    stmt = select(Channel).order_by(Channel.id)
    if status is not None:
        stmt = stmt.where(Channel.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def add_channel(
    session: AsyncSession,
    *,
    telegram_channel_id: int,
    channel_title: str,
    channel_username: str | None = None,
) -> Channel:
    """Start tracking a channel. Idempotent — see module docstring."""
    existing = await get_channel_by_telegram_id(session, telegram_channel_id)
    if existing is not None:
        existing.channel_title = channel_title
        existing.channel_username = channel_username
        if existing.status != ChannelStatus.ACTIVE:
            existing.status = ChannelStatus.ACTIVE
        await session.commit()
        await session.refresh(existing)
        return existing

    channel = Channel(
        telegram_channel_id=telegram_channel_id,
        channel_title=channel_title,
        channel_username=channel_username,
        status=ChannelStatus.ACTIVE,
    )
    session.add(channel)
    await session.flush()  # need channel.id for the ScanState FK

    # A fresh channel always starts in scan_phase=INITIAL (the column
    # default) — this is what makes the next scan bound itself to the
    # last N days rather than trying to backfill everything.
    session.add(ScanState(channel_id=channel.id))

    await session.commit()
    await session.refresh(channel)
    return channel


async def enable_channel(session: AsyncSession, channel_id: int) -> Channel:
    channel = await get_channel(session, channel_id)
    channel.status = ChannelStatus.ACTIVE
    await session.commit()
    await session.refresh(channel)
    return channel


async def disable_channel(session: AsyncSession, channel_id: int) -> Channel:
    """Soft-"remove" a channel. Its scan_state/source_messages are kept
    untouched, so re-enabling later resumes exactly where it left off
    instead of restarting the initial scan."""
    channel = await get_channel(session, channel_id)
    channel.status = ChannelStatus.DISABLED
    await session.commit()
    await session.refresh(channel)
    return channel
