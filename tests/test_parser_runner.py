"""
Tests for `app.parsing.parser_runner.process_pending_messages`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

import pytest

from app.models.enums import SourceMessageStatus
from app.parsing.parser_runner import process_pending_messages
from tests.factories import make_channel, make_source_message

pytestmark = pytest.mark.asyncio


async def test_processes_only_pending_messages(db_session):
    channel = await make_channel(db_session, telegram_channel_id=200)
    pending_1 = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="Nike Shoes\n30 KWD"
    )
    pending_2 = await make_source_message(
        db_session, channel, telegram_message_id=2, raw_text=None
    )
    already_processed = await make_source_message(
        db_session, channel, telegram_message_id=3, raw_text="Old Item\n10 KWD"
    )
    already_processed.status = SourceMessageStatus.PROCESSED
    await db_session.flush()

    summary = await process_pending_messages(db_session)

    processed_ids = {r.source_message_id for r in summary.results}
    assert processed_ids == {pending_1.id, pending_2.id}
    assert already_processed.id not in processed_ids

    assert summary.messages_processed == 2
    assert summary.created == 1
    assert summary.ignored == 1
    assert summary.duplicates_linked == 0
    assert summary.failed == 0


async def test_summary_counts_duplicates_correctly(db_session):
    channel = await make_channel(db_session, telegram_channel_id=201)
    await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="Nike Shoes\n30 KWD"
    )
    await make_source_message(
        db_session, channel, telegram_message_id=2, raw_text="nike   shoes\n30 KWD"
    )

    summary = await process_pending_messages(db_session)

    assert summary.messages_processed == 2
    assert summary.created == 1
    assert summary.duplicates_linked == 1


async def test_respects_limit(db_session):
    channel = await make_channel(db_session, telegram_channel_id=202)
    for i in range(5):
        await make_source_message(
            db_session, channel, telegram_message_id=i, raw_text=f"Item {i}\n{10 + i} KWD"
        )

    summary = await process_pending_messages(db_session, limit=2)

    assert summary.messages_processed == 2
