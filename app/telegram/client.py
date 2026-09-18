"""
Telegram client abstraction.

`TelegramClient` is a `Protocol` (structural interface) that the rest of
`app/telegram/` codes against — `scan_service.py` never imports Telethon
directly. This makes scan/ingestion logic fully unit-testable with an
in-memory fake client, and keeps a future client-library swap (Telethon ->
Pyrogram or similar) contained to this one file.

`TelethonClient` is the real implementation, using a Telegram USER session
(MTProto via Telethon) rather than a Bot API token. This is a deliberate,
required choice: the Bot API cannot read a channel's message history from
before the bot joined, which the initial 5-day backfill requires. A user
session (logged in once interactively to produce a `.session` file) can.

IMPORTANT: this module could not be exercised against the real Telegram
network in the environment it was written in (no network access). The
Telethon call shapes below follow Telethon's documented API as of this
writing, but have not been run against a live Telegram account — verify
`iter_messages(...)` behavior (in particular `offset_date` + `reverse`
interaction) against a real session before relying on this in production.

IDENTIFIER SEMANTICS: media identifiers produced here (`source_media_id`,
`source_media_unique_id` on `TelegramMediaDTO`) come from MTProto
(Telethon's `Photo.id` / `Document.id` / `access_hash`), which is a
DIFFERENT identifier system from the Telegram Bot API's `file_id` /
`file_unique_id`. See `_to_message_dto()` below for exactly what is (and
is not) populated and why — in particular, `source_media_unique_id` is
always `None` for this client, not derived from `access_hash`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from app.telegram.schemas import TelegramChannelInfo, TelegramMediaDTO, TelegramMessageDTO

logger = logging.getLogger(__name__)


class TelegramClient(Protocol):
    """Structural interface every Telegram client implementation follows."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def download_message_media(
        self,
        telegram_channel_id: int,
        telegram_message_id: int,
        target_dir: str,
    ) -> list[tuple[str, str]]:
        """Download a message media item and return ``(media_id, relative_path)`` pairs.

        The method is intentionally implemented only by the real Telethon client
        (see `TelethonClient` below); ingestion remains usable with lightweight
        test fakes that do not download media.
        """
        ...

    async def resolve_channel(self, identifier: str | int) -> TelegramChannelInfo:
        """Resolve a channel by @username, invite link, or numeric id."""
        ...

    def iter_messages(
        self,
        telegram_channel_id: int,
        *,
        min_id: int | None = None,
        since: datetime | None = None,
    ) -> AsyncIterator[TelegramMessageDTO]:
        """Yield messages oldest-to-newest.

        `min_id`, if given, excludes messages at or before that Telegram
        message id (used for INCREMENTAL/CATCHUP resumption).
        `since`, if given, excludes messages older than that date (used to
        bound INITIAL/CATCHUP scans to the last N days).
        Both may be given together (CATCHUP uses both).
        """
        ...


class TelethonClient:
    """Real Telegram client, backed by Telethon (MTProto user session)."""

    def __init__(self, api_id: int, api_hash: str, session_name: str) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_name = session_name
        self._client = None  # type: ignore[var-annotated]

    async def connect(self) -> None:
        from telethon import TelegramClient as _Telethon  # local import: optional dependency

        self._client = _Telethon(self._session_name, self._api_id, self._api_hash)
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Telegram session is not authorized. Run the Telethon login "
                "flow once interactively to create a valid session file "
                f"('{self._session_name}.session') before starting the monitor."
            )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()

    async def download_message_media(
        self,
        telegram_channel_id: int,
        telegram_message_id: int,
        target_dir: str,
    ) -> list[tuple[str, str]]:
        """Download a message media item and return ``(media_id, relative_path)`` pairs.

        The method is intentionally implemented only by the real Telethon client;
        ingestion remains usable with lightweight test fakes that do not download media.
        """
        from pathlib import Path
        message = await self._client.get_messages(telegram_channel_id, ids=telegram_message_id)
        if message is None or not getattr(message, "media", None):
            return []

        root = Path(target_dir)
        root.mkdir(parents=True, exist_ok=True)
        media_id = getattr(getattr(message, "photo", None), "id", None)
        if media_id is None:
            media_id = getattr(getattr(message, "document", None), "id", None)
        if media_id is None:
            return []

        ext = ""
        document = getattr(message, "document", None)
        if document is not None:
            for attribute in getattr(document, "attributes", []) or []:
                filename = getattr(attribute, "file_name", None)
                if filename and "." in filename:
                    ext = "." + filename.rsplit(".", 1)[1].lower()
                    break
        if not ext:
            if getattr(message, "video", None) is not None:
                ext = ".mp4"
            elif getattr(getattr(document, "mime_type", None), "startswith", lambda *_: False)("video/"):
                ext = ".mp4"
            elif getattr(message, "photo", None) is not None:
                ext = ".jpg"
            else:
                ext = ".bin"

        filename = f"{int(media_id)}{ext}"
        destination = root / filename
        downloaded = await self._client.download_media(message, file=str(destination))
        if not downloaded:
            return []
        return [(str(media_id), str(destination))]

    async def resolve_channel(self, identifier: str | int) -> TelegramChannelInfo:
        entity = await self._client.get_entity(identifier)
        return TelegramChannelInfo(
            telegram_channel_id=entity.id,
            channel_username=getattr(entity, "username", None),
            channel_title=getattr(entity, "title", None) or str(entity.id),
        )

    async def iter_messages(
        self,
        telegram_channel_id: int,
        *,
        min_id: int | None = None,
        since: datetime | None = None,
    ) -> AsyncIterator[TelegramMessageDTO]:
        async for message in self._client.iter_messages(
            telegram_channel_id,
            min_id=min_id or 0,
            offset_date=since,
            reverse=True,  # oldest-to-newest, so scan_state's high-water
            # mark always advances monotonically as we ingest
        ):
            yield _to_message_dto(message)


def _to_message_dto(message) -> TelegramMessageDTO:
    """Map a Telethon `Message` object to our client-agnostic DTO.

    IMPORTANT — identifier semantics: Telethon's `Photo.id` / `Document.id`
    are MTProto media identifiers, stable for the same underlying file
    (e.g. consistent across forwards/reposts of the exact same upload).
    They are NOT the Telegram Bot API's `file_id`. We use them for
    `source_media_id` because they're the closest thing MTProto offers to
    "an id for this attachment" — good enough for idempotent re-ingestion
    of the same message, but not to be assumed interchangeable with a Bot
    API file_id if a bot-based client is ever added later.

    `access_hash` is deliberately NEVER used here: it is a per-request
    authorization token for fetching that specific media (tied to the
    requesting session/context), not a stable content identifier — using
    it as a "unique id" would be both semantically wrong and unreliable.
    Because of this, `source_media_unique_id` is always left `None` for
    Telethon-sourced media: MTProto has no direct, stable, cross-context
    equivalent to the Bot API's `file_unique_id` without extra work (e.g.
    hashing the downloaded bytes, which belongs to a later duplicate-
    detection phase, not here).
    """
    media_items: list[TelegramMediaDTO] = []
    if getattr(message, "photo", None):
        photo = message.photo
        media_items.append(
            TelegramMediaDTO(
                source_media_id=str(photo.id),
                source_media_unique_id=None,
                media_type="PHOTO",
            )
        )
    if getattr(message, "video", None):
        video = message.video
        media_items.append(
            TelegramMediaDTO(
                source_media_id=str(video.id),
                source_media_unique_id=None,
                media_type="VIDEO",
                duration_seconds=getattr(video, "duration", None),
            )
        )
    if getattr(message, "document", None) and not media_items:
        doc = message.document
        media_items.append(
            TelegramMediaDTO(
                source_media_id=str(doc.id),
                source_media_unique_id=None,
                media_type="DOCUMENT",
                file_size_bytes=getattr(doc, "size", None),
            )
        )

    return TelegramMessageDTO(
        telegram_message_id=message.id,
        message_date=message.date,
        raw_text=message.raw_text or message.message or None,
        media_group_id=getattr(message, "grouped_id", None),
        media=media_items,
    )
