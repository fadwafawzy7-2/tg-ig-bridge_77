"""Manual capture: turn a message forwarded (or sent directly) to the
dashboard bot into a SourceMessage + Media + Product - with NO Telethon,
no api_id/api_hash, and, deliberately, NO reading of whatever text/caption
came attached to the message.

The person picks what to forward and then TYPES the caption themselves
(the dashboard bot asks for it right after they pick POST/REEL/STORY -
see app/dashboard/bot.py). Whatever text Telegram attached to the
forwarded item (if any) is discarded unread; it is never parsed for a
title/price and never shown to the AI. This intentionally bypasses
app/parsing/text_parser.py entirely - that module exists to auto-extract
fields from a channel's own post text, which is exactly what this flow
does not do.

Because there is no text to fingerprint, duplicate detection here is
based on the media file's own Telegram identity (file_unique_id) instead
of on parsed title/price - forwarding the exact same photo/video twice is
still caught; two different photos are always two different products,
even if the (still-empty-at-this-point) caption will later say the same
thing.

Channel attribution: identical to the previous version of this module -
see `_origin_from_message`.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.dashboard.bot_api import TelegramBotAPI
from app.media import cloud_storage
from app.media.storage import storage_root
from app.models.channel import Channel
from app.models.enums import ChannelStatus, MediaType, ProductStatus, SourceMessageStatus
from app.models.media import Media
from app.models.product import Product
from app.models.product_source_message import ProductSourceMessage
from app.models.source_message import SourceMessage
from app.parsing.product_service import ProcessResult
from app.products_codes import generate_product_code

logger = logging.getLogger(__name__)

# Telegram's real channel/supergroup ids are large *negative* numbers.
# Offsetting admin ids far into positive territory guarantees the
# fallback pseudo-channel can never collide with a genuine channel id.
_UNKNOWN_ORIGIN_ID_OFFSET = 9_000_000_000_000


async def _get_or_create_channel(
    session: AsyncSession, *, telegram_channel_id: int, title: str, username: str | None
) -> Channel:
    result = await session.execute(
        select(Channel).where(Channel.telegram_channel_id == telegram_channel_id)
    )
    channel = result.scalars().first()
    if channel is not None:
        return channel
    channel = Channel(
        telegram_channel_id=telegram_channel_id,
        channel_username=username,
        channel_title=title,
        status=ChannelStatus.ACTIVE,
    )
    session.add(channel)
    await session.flush()
    return channel


def _origin_from_message(message: dict) -> tuple[int | None, str | None, str | None, int | None]:
    """Best-effort extraction of the real origin channel.

    Returns (telegram_channel_id, title, username, origin_message_id).
    All four are None together when Telegram doesn't expose the origin.
    """
    origin = message.get("forward_origin")
    if isinstance(origin, dict) and origin.get("type") == "channel":
        chat = origin.get("chat") or {}
        chat_id = chat.get("id")
        title = chat.get("title")
        if chat_id is not None and title:
            return int(chat_id), str(title), chat.get("username"), origin.get("message_id")

    legacy_chat = message.get("forward_from_chat")
    if isinstance(legacy_chat, dict) and legacy_chat.get("type") == "channel":
        chat_id = legacy_chat.get("id")
        title = legacy_chat.get("title")
        if chat_id is not None and title:
            return int(chat_id), str(title), legacy_chat.get("username"), message.get("forward_from_message_id")

    return None, None, None, None


def _best_photo(message: dict) -> dict | None:
    sizes = message.get("photo")
    if not sizes:
        return None
    return max(sizes, key=lambda s: s.get("file_size") or (s.get("width", 0) * s.get("height", 0)))


async def _download_media(api: TelegramBotAPI, settings, file_id: str, suggested_ext: str) -> tuple[str, int]:
    """Download one Telegram file and persist it (local disk or S3-compatible storage)."""
    descriptor = await api.get_file(file_id)
    tg_path = descriptor["file_path"]
    content = await api.download_file_bytes(tg_path)
    ext = Path(tg_path).suffix or suggested_ext
    object_key = f"manual/{file_id}{ext}"

    if settings.MEDIA_STORAGE_BACKEND.lower() in ("s3", "r2"):
        tmp_path = Path("/tmp") / f"{file_id}{ext}"
        tmp_path.write_bytes(content)
        try:
            file_path = cloud_storage.upload_file(str(tmp_path), object_key)
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        root = storage_root(settings.MEDIA_STORAGE_DIR)
        destination = root / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        file_path = object_key

    return file_path, len(content)


def _media_content_hash(file_unique_ids: list[str]) -> str:
    fingerprint = "|".join(sorted(file_unique_ids))
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


async def _find_existing_product_by_hash(session: AsyncSession, content_hash: str) -> Product | None:
    result = await session.execute(select(Product).where(Product.content_hash == content_hash))
    return result.scalars().first()


async def _link_to_existing_product(session: AsyncSession, product: Product, source_message: SourceMessage) -> None:
    stmt = pg_insert(ProductSourceMessage).values(
        product_id=product.id, source_message_id=source_message.id
    ).on_conflict_do_nothing(index_elements=["product_id", "source_message_id"])
    await session.execute(stmt)


async def ingest_forwarded_message(session: AsyncSession, api: TelegramBotAPI, message: dict) -> ProcessResult:
    """Create a SourceMessage (+ Media) from a message sent/forwarded to the
    dashboard bot, then create/link its Product directly - deliberately
    without ever reading `message["caption"]`/`message["text"]`. The
    caption comes later, typed by the person (see app/dashboard/bot.py).
    """
    settings = get_settings()
    admin_chat_id = message["chat"]["id"]
    channel_id, title, username, origin_message_id = _origin_from_message(message)

    if channel_id is not None and origin_message_id is not None:
        channel = await _get_or_create_channel(
            session, telegram_channel_id=channel_id, title=title, username=username
        )
        source_telegram_message_id = origin_message_id
        message_link = f"https://t.me/{username}/{origin_message_id}" if username else None
    else:
        channel = await _get_or_create_channel(
            session,
            telegram_channel_id=_UNKNOWN_ORIGIN_ID_OFFSET + abs(admin_chat_id),
            title="تحويل يدوي (مصدر غير معروف)",
            username=None,
        )
        source_telegram_message_id = message["message_id"]
        message_link = None

    message_date = datetime.fromtimestamp(message.get("date") or time.time(), tz=timezone.utc)

    photo = _best_photo(message)
    video = message.get("video")
    if photo is None and video is None:
        return ProcessResult(source_message_id=0, outcome="ignored", error="no photo/video attached")

    source_message = SourceMessage(
        channel_id=channel.id,
        telegram_message_id=source_telegram_message_id,
        message_date=message_date,
        message_link=message_link,
        raw_text=None,  # deliberately never read - see module docstring
        has_media=True,
        status=SourceMessageStatus.PENDING,
    )
    session.add(source_message)
    channel_db_id = channel.id  # snapshot before rollback expires the object
    try:
        await session.commit()
    except IntegrityError:
        # Same (channel_id, telegram_message_id) forwarded again. This is
        # not a transient failure - retrying it will always fail the same
        # way - so it must NOT propagate up to run_once() and leave the
        # Telegram update offset stuck (which would otherwise replay this
        # same forward, and block every update behind it, on every cycle).
        await session.rollback()
        existing = await session.execute(
            select(SourceMessage).where(
                SourceMessage.channel_id == channel_db_id,
                SourceMessage.telegram_message_id == source_telegram_message_id,
            )
        )
        existing_row = existing.scalars().first()
        existing_product_id: int | None = None
        if existing_row is not None:
            link = await session.execute(
                select(ProductSourceMessage.product_id).where(
                    ProductSourceMessage.source_message_id == existing_row.id
                )
            )
            existing_product_id = link.scalars().first()
        return ProcessResult(
            source_message_id=existing_row.id if existing_row is not None else 0,
            outcome="duplicate_linked",
            product_id=existing_product_id,
        )
    await session.refresh(source_message)

    try:
        file_unique_ids: list[str] = []
        media_rows: list[Media] = []

        if photo is not None:
            file_path, size = await _download_media(api, settings, photo["file_id"], ".jpg")
            media_rows.append(Media(
                source_message_id=source_message.id,
                media_type=MediaType.PHOTO,
                telegram_file_id=photo["file_id"],
                telegram_file_unique_id=photo.get("file_unique_id"),
                file_path=file_path,
                file_size_bytes=size,
                width=photo.get("width"),
                height=photo.get("height"),
            ))
            if photo.get("file_unique_id"):
                file_unique_ids.append(photo["file_unique_id"])
        if video is not None:
            file_path, size = await _download_media(api, settings, video["file_id"], ".mp4")
            media_rows.append(Media(
                source_message_id=source_message.id,
                media_type=MediaType.VIDEO,
                telegram_file_id=video["file_id"],
                telegram_file_unique_id=video.get("file_unique_id"),
                file_path=file_path,
                file_size_bytes=size,
                width=video.get("width"),
                height=video.get("height"),
                duration_seconds=video.get("duration"),
            ))
            if video.get("file_unique_id"):
                file_unique_ids.append(video["file_unique_id"])

        for row in media_rows:
            session.add(row)
        await session.flush()
    except Exception:
        logger.exception(
            "Failed to download media for manual capture, source_message_id=%s", source_message.id
        )
        await session.rollback()
        source_message.status = SourceMessageStatus.FAILED
        await session.commit()
        return ProcessResult(source_message_id=source_message.id, outcome="failed", error="media_download_failed")

    content_hash = _media_content_hash(file_unique_ids) if file_unique_ids else None

    if content_hash is not None:
        existing_product = await _find_existing_product_by_hash(session, content_hash)
        if existing_product is not None:
            await _link_to_existing_product(session, existing_product, source_message)
            for row in media_rows:
                row.product_id = existing_product.id
            source_message.status = SourceMessageStatus.PROCESSED
            await session.commit()
            return ProcessResult(
                source_message_id=source_message.id,
                outcome="duplicate_linked",
                product_id=existing_product.id,
            )

    product = Product(
        product_code=generate_product_code(),
        primary_source_message_id=source_message.id,
        channel_id=channel.id,
        source_channel_id=channel.telegram_channel_id,
        source_channel_name=channel.channel_title,
        source_message_id=source_message.telegram_message_id,
        source_message_date=source_message.message_date,
        source_message_link=source_message.message_link,
        status=ProductStatus.PARSED,
        content_hash=content_hash,
    )
    session.add(product)
    await session.flush()
    for row in media_rows:
        row.product_id = product.id
    source_message.status = SourceMessageStatus.PROCESSED
    await session.commit()
    await session.refresh(product)

    return ProcessResult(source_message_id=source_message.id, outcome="created", product_id=product.id)
