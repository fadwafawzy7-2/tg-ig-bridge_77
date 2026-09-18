"""
Idempotent ingestion of Telegram messages (and their media) into
`source_messages` / `media`.

This is the literal "Telegram is source of truth, never delete/replace
source data" boundary: `ingest_message()` only ever INSERTs. Re-ingesting
a message it has already seen (same `channel_id` + `telegram_message_id`)
is a no-op — the existing row is returned untouched, never updated or
overwritten. No product parsing/extraction happens here; this module's
only job is durably recording what Telegram sent.

Uses Postgres `INSERT ... ON CONFLICT DO NOTHING` (rather than
select-then-insert) so a race between two overlapping scans of the same
channel can't create duplicate rows or raise a surprise IntegrityError.
Each message is committed as its own small transaction — restart-safe: a
crash mid-scan loses at most the message currently being ingested, never
already-ingested ones.

See `_ingest_media()` below for a note on `media.telegram_file_id` /
`telegram_file_unique_id` column-naming vs. what a Telethon-based client
actually populates them with.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.models.media import Media
from app.models.source_message import SourceMessage
from app.telegram.schemas import TelegramMessageDTO


def _build_message_link(channel: Channel, telegram_message_id: int) -> str | None:
    """Only buildable for channels with a public @username."""
    if not channel.channel_username:
        return None
    return f"https://t.me/{channel.channel_username}/{telegram_message_id}"


async def ingest_message(
    session: AsyncSession, channel: Channel, message: TelegramMessageDTO
) -> SourceMessage:
    insert_stmt = (
        pg_insert(SourceMessage)
        .values(
            channel_id=channel.id,
            telegram_message_id=message.telegram_message_id,
            message_date=message.message_date,
            message_link=_build_message_link(channel, message.telegram_message_id),
            raw_text=message.raw_text,
            has_media=message.has_media,
            media_group_id=message.media_group_id,
        )
        .on_conflict_do_nothing(
            index_elements=["channel_id", "telegram_message_id"],
        )
        .returning(SourceMessage.id)
    )
    result = await session.execute(insert_stmt)
    new_id = result.scalar_one_or_none()

    if new_id is None:
        # Already ingested (by an earlier scan, or a concurrent one) —
        # fetch the existing row rather than touching it. Source data is
        # never updated/replaced once recorded.
        existing = await session.execute(
            select(SourceMessage).where(
                SourceMessage.channel_id == channel.id,
                SourceMessage.telegram_message_id == message.telegram_message_id,
            )
        )
        source_message = existing.scalar_one()
        await session.commit()
        return source_message

    await session.flush()
    source_message = await session.get(SourceMessage, new_id)
    assert source_message is not None

    for media_dto in message.media:
        await _ingest_media(session, source_message, media_dto)

    await session.commit()
    return source_message


async def _ingest_media(session: AsyncSession, source_message: SourceMessage, media_dto) -> None:
    """Map a `TelegramMediaDTO` onto the `media` table.

    NOTE on column naming vs. DTO naming: the `media` table's columns are
    named `telegram_file_id` / `telegram_file_unique_id` (Phase 2 schema,
    unchanged here). The DTO fields feeding them are named generically
    (`source_media_id` / `source_media_unique_id`) precisely because, for
    this project's Telethon (MTProto) client, they do NOT hold Bot API
    file_id/file_unique_id values — see `client.py` for what they actually
    contain. Do not assume these columns hold Bot-API-compatible values
    when reading them back.
    """
    insert_stmt = (
        pg_insert(Media)
        .values(
            source_message_id=source_message.id,
            media_type=media_dto.media_type,
            telegram_file_id=media_dto.source_media_id,
            telegram_file_unique_id=media_dto.source_media_unique_id,
            width=media_dto.width,
            height=media_dto.height,
            duration_seconds=media_dto.duration_seconds,
            file_size_bytes=media_dto.file_size_bytes,
        )
        .on_conflict_do_nothing(
            index_elements=["source_message_id", "telegram_file_id"],
        )
    )
    await session.execute(insert_stmt)
