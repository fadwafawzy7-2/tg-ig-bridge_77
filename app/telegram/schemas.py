"""
Plain data-transfer objects for Telegram messages/media.

Deliberately independent of any specific Telegram client library
(Telethon, Pyrogram, ...): the rest of `app/telegram/` (ingestion,
scanning) only ever depends on these dataclasses, not on Telethon types
directly. That keeps ingestion/scan logic unit-testable with a fake
client and insulates the codebase from a future client-library swap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TelegramMediaDTO:
    """One photo/video/document attached to a Telegram message.

    Only metadata Telegram hands us for free is captured here — no
    download, no perceptual hashing, no content analysis. That belongs to
    a later phase; Phase 3 only records that the media exists and how to
    fetch it later.

    `source_media_id` / `source_media_unique_id` are deliberately named
    generically rather than "telegram_file_id" / "telegram_file_unique_id":
    those are specific Telegram BOT API concepts, and this project's
    client is an MTProto USER session (Telethon), not a bot. Telethon
    exposes a different identifier system (`Photo.id` / `Document.id`,
    `access_hash`, `file_reference`) that must not be conflated with the
    Bot API's. See `client.py::_to_message_dto` for exactly what each
    field holds for the Telethon client, and DO NOT treat
    `source_media_unique_id` as a duplicate-detection fingerprint unless
    you've verified what the client actually put in it — it is None for
    the Telethon client (see below), since MTProto has no direct,
    stable, cross-context equivalent to the Bot API's `file_unique_id`.
    """

    source_media_id: str
    source_media_unique_id: str | None
    media_type: str  # matches app.models.enums.MediaType values
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    file_size_bytes: int | None = None


@dataclass(frozen=True)
class TelegramMessageDTO:
    """One Telegram channel message, as read off the wire.

    `raw_text` is the ONLY product-relevant content captured — per project
    rules, product specs are never extracted from images/media here (or
    anywhere in this phase); the source text is the sole reference.
    """

    telegram_message_id: int
    message_date: datetime
    raw_text: str | None
    media_group_id: int | None
    media: list[TelegramMediaDTO] = field(default_factory=list)

    @property
    def has_media(self) -> bool:
        return len(self.media) > 0


@dataclass(frozen=True)
class TelegramChannelInfo:
    """Resolved identity of a Telegram channel, used when adding one."""

    telegram_channel_id: int
    channel_username: str | None
    channel_title: str
