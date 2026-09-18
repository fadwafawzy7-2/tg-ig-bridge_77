"""
Tests for `app.telegram.client._to_message_dto` — specifically, that
Telethon/MTProto identifiers (`Photo.id` / `Document.id` / `access_hash`)
are never mislabeled as Telegram Bot API `file_id` / `file_unique_id`.

Uses plain `SimpleNamespace` stand-ins for Telethon's message/photo/video/
document objects (duck-typed via `getattr`, exactly like the real ones) —
no Telethon installation, network, or database needed. This is the
second Phase 3 test module (after `test_scan_window.py`) that needs
nothing but the standard library, and was actually executed in this
environment to confirm it passes.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.telegram.client import _to_message_dto

MESSAGE_DATE = datetime(2026, 1, 10, 9, 0, 0, tzinfo=timezone.utc)


def _fake_message(**overrides):
    base = dict(
        id=42,
        date=MESSAGE_DATE,
        raw_text="hello",
        message="hello",
        grouped_id=None,
        photo=None,
        video=None,
        document=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_photo_media_uses_mtproto_id_not_access_hash():
    photo = SimpleNamespace(id=111, access_hash=999999)
    message = _fake_message(photo=photo)

    dto = _to_message_dto(message)

    assert len(dto.media) == 1
    media = dto.media[0]
    assert media.media_type == "PHOTO"
    # The MTProto media id is fine to use as our own idempotency-key
    # material...
    assert media.source_media_id == "111"
    # ...but access_hash must NEVER end up as a "unique id" — it's a
    # per-context access token, not a stable content identifier, and is
    # not the Bot API's file_unique_id.
    assert media.source_media_unique_id is None
    assert "999999" not in (media.source_media_unique_id or "")


def test_video_media_uses_mtproto_id_not_access_hash():
    video = SimpleNamespace(id=222, access_hash=888888, duration=30)
    message = _fake_message(video=video)

    dto = _to_message_dto(message)

    assert len(dto.media) == 1
    media = dto.media[0]
    assert media.media_type == "VIDEO"
    assert media.source_media_id == "222"
    assert media.source_media_unique_id is None
    assert media.duration_seconds == 30


def test_document_media_uses_mtproto_id_not_access_hash():
    document = SimpleNamespace(id=333, access_hash=777777, size=2048)
    message = _fake_message(document=document)

    dto = _to_message_dto(message)

    assert len(dto.media) == 1
    media = dto.media[0]
    assert media.media_type == "DOCUMENT"
    assert media.source_media_id == "333"
    assert media.source_media_unique_id is None
    assert media.file_size_bytes == 2048


def test_document_is_ignored_when_photo_or_video_already_present():
    """A Telethon message can carry a `document` attribute even for
    photos/videos (Telegram's underlying representation); only the more
    specific type should be recorded, not a duplicate DOCUMENT entry."""
    photo = SimpleNamespace(id=111, access_hash=999999)
    document = SimpleNamespace(id=333, access_hash=777777, size=2048)
    message = _fake_message(photo=photo, document=document)

    dto = _to_message_dto(message)

    assert len(dto.media) == 1
    assert dto.media[0].media_type == "PHOTO"


def test_message_without_media_has_no_media_items():
    message = _fake_message()

    dto = _to_message_dto(message)

    assert dto.media == []
    assert dto.has_media is False


def test_message_core_fields_are_mapped():
    message = _fake_message(id=7, grouped_id=12345, raw_text="Sale! 20 KWD")

    dto = _to_message_dto(message)

    assert dto.telegram_message_id == 7
    assert dto.message_date == MESSAGE_DATE
    assert dto.raw_text == "Sale! 20 KWD"
    assert dto.media_group_id == 12345
