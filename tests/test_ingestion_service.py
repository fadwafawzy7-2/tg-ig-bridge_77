"""
Tests for `app.telegram.ingestion_service.ingest_message`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.media import Media
from app.models.source_message import SourceMessage
from app.telegram.ingestion_service import ingest_message
from app.telegram.schemas import TelegramMediaDTO
from tests.factories import make_channel
from tests.fakes import make_message

pytestmark = pytest.mark.asyncio

MESSAGE_DATE = datetime(2026, 1, 10, 9, 0, 0, tzinfo=timezone.utc)


async def test_ingest_message_persists_expected_fields(db_session):
    channel = await make_channel(db_session, telegram_channel_id=10, title="Store A")
    message = make_message(1, MESSAGE_DATE, raw_text="Nike shoes, size 42, 30 KWD")

    source_message = await ingest_message(db_session, channel, message)

    assert source_message.channel_id == channel.id
    assert source_message.telegram_message_id == 1
    assert source_message.message_date == MESSAGE_DATE
    assert source_message.raw_text == "Nike shoes, size 42, 30 KWD"
    assert source_message.has_media is False


async def test_ingest_message_builds_link_only_for_public_channels(db_session):
    public_channel = await make_channel(db_session, telegram_channel_id=20, title="Public")
    public_channel.channel_username = "publicstore"
    await db_session.flush()

    private_channel = await make_channel(db_session, telegram_channel_id=21, title="Private")

    public_msg = await ingest_message(db_session, public_channel, make_message(1, MESSAGE_DATE))
    private_msg = await ingest_message(db_session, private_channel, make_message(1, MESSAGE_DATE))

    assert public_msg.message_link == "https://t.me/publicstore/1"
    assert private_msg.message_link is None


async def test_ingesting_the_same_message_twice_is_a_no_op(db_session):
    channel = await make_channel(db_session, telegram_channel_id=30)

    first = await ingest_message(
        db_session, channel, make_message(7, MESSAGE_DATE, raw_text="Original text")
    )
    # Simulate re-scanning the same message (e.g. after a restart). Even
    # if the "new" copy claims different text, the ORIGINAL source data
    # must never be overwritten.
    second = await ingest_message(
        db_session, channel, make_message(7, MESSAGE_DATE, raw_text="Different text!!")
    )

    assert first.id == second.id
    assert second.raw_text == "Original text"

    rows = (
        (
            await db_session.execute(
                select(SourceMessage).where(
                    SourceMessage.channel_id == channel.id,
                    SourceMessage.telegram_message_id == 7,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_ingest_message_with_media_creates_linked_media_rows(db_session):
    channel = await make_channel(db_session, telegram_channel_id=40)
    message = make_message(1, MESSAGE_DATE)
    message.media.append(
        TelegramMediaDTO(
            source_media_id="media-1",
            source_media_unique_id=None,
            media_type="PHOTO",
            width=1080,
            height=1080,
        )
    )

    source_message = await ingest_message(db_session, channel, message)

    media_rows = (
        (
            await db_session.execute(
                select(Media).where(Media.source_message_id == source_message.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(media_rows) == 1
    # `media.telegram_file_id` is the DB column name (Phase 2, unchanged);
    # it holds whatever `source_media_id` the client reported (an MTProto
    # media id here, NOT a Bot API file_id — see client.py).
    assert media_rows[0].telegram_file_id == "media-1"
    assert media_rows[0].telegram_file_unique_id is None
    assert media_rows[0].product_id is None  # not parsed/assigned in Phase 3


async def test_ingesting_same_media_twice_does_not_duplicate(db_session):
    channel = await make_channel(db_session, telegram_channel_id=50)
    media_item = TelegramMediaDTO(
        source_media_id="media-dup", source_media_unique_id=None, media_type="PHOTO"
    )

    message = make_message(1, MESSAGE_DATE)
    message.media.append(media_item)
    source_message = await ingest_message(db_session, channel, message)

    # Re-ingest the exact same message+media (e.g. overlapping catch-up
    # window) — must not create a second Media row.
    message_again = make_message(1, MESSAGE_DATE)
    message_again.media.append(media_item)
    await ingest_message(db_session, channel, message_again)

    media_rows = (
        (
            await db_session.execute(
                select(Media).where(Media.source_message_id == source_message.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(media_rows) == 1
