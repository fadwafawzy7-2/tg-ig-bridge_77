"""
Integration tests for `app.telegram.scan_service.scan_channel`, using
`FakeTelegramClient` — no real Telegram network involved. Needs a real
Postgres (see `db_session` in conftest.py); skipped automatically if one
isn't reachable.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.enums import ScanPhase
from app.models.error_log import ErrorLog
from app.models.source_message import SourceMessage
from app.telegram import channel_service
from app.telegram.scan_service import scan_channel
from tests.fakes import FakeTelegramClient, FlakyFakeTelegramClient, make_message

pytestmark = pytest.mark.asyncio

INITIAL_SCAN_DAYS = 5
CATCHUP_GAP_MINUTES = 30


def _now():
    return datetime.now(timezone.utc)


async def test_initial_scan_ingests_recent_messages_and_completes(db_session):
    channel = await channel_service.add_channel(
        db_session, telegram_channel_id=1000, channel_title="Shop"
    )
    now = _now()
    client = FakeTelegramClient(
        {
            1000: [
                make_message(1, now - timedelta(days=3)),
                make_message(2, now - timedelta(days=1)),
            ]
        }
    )

    result = await scan_channel(
        db_session,
        client,
        channel,
        initial_scan_days=INITIAL_SCAN_DAYS,
        catchup_gap_minutes=CATCHUP_GAP_MINUTES,
    )

    assert result.ok
    assert result.phase is ScanPhase.INITIAL
    assert result.messages_ingested == 2

    await db_session.refresh(channel, attribute_names=["scan_state"])
    assert channel.scan_state.initial_scan_completed_at is not None
    assert channel.scan_state.scan_phase is ScanPhase.INCREMENTAL
    assert channel.scan_state.last_processed_message_id == 2


async def test_initial_scan_ignores_messages_older_than_the_window(db_session):
    channel = await channel_service.add_channel(
        db_session, telegram_channel_id=1001, channel_title="Shop"
    )
    now = _now()
    client = FakeTelegramClient(
        {
            1001: [
                make_message(1, now - timedelta(days=20)),  # too old, must be skipped
                make_message(2, now - timedelta(days=1)),
            ]
        }
    )

    result = await scan_channel(
        db_session,
        client,
        channel,
        initial_scan_days=INITIAL_SCAN_DAYS,
        catchup_gap_minutes=CATCHUP_GAP_MINUTES,
    )

    assert result.messages_ingested == 1
    rows = (
        (
            await db_session.execute(
                select(SourceMessage).where(SourceMessage.channel_id == channel.id)
            )
        )
        .scalars()
        .all()
    )
    assert [r.telegram_message_id for r in rows] == [2]


async def test_incremental_scan_only_ingests_new_messages(db_session):
    channel = await channel_service.add_channel(
        db_session, telegram_channel_id=1002, channel_title="Shop"
    )
    now = _now()
    client = FakeTelegramClient({1002: [make_message(1, now - timedelta(hours=1))]})

    first_result = await scan_channel(
        db_session,
        client,
        channel,
        initial_scan_days=INITIAL_SCAN_DAYS,
        catchup_gap_minutes=CATCHUP_GAP_MINUTES,
    )
    assert first_result.phase is ScanPhase.INITIAL
    assert first_result.messages_ingested == 1

    # A new message arrives; the channel is scanned again shortly after
    # (well within the catch-up gap threshold) -> plain INCREMENTAL.
    client.messages_by_channel[1002].append(make_message(2, now))
    second_result = await scan_channel(
        db_session,
        client,
        channel,
        initial_scan_days=INITIAL_SCAN_DAYS,
        catchup_gap_minutes=CATCHUP_GAP_MINUTES,
    )

    assert second_result.phase is ScanPhase.INCREMENTAL
    assert second_result.messages_ingested == 1  # only the new one

    await db_session.refresh(channel, attribute_names=["scan_state"])
    assert channel.scan_state.last_processed_message_id == 2


async def test_scan_failure_is_logged_and_does_not_advance_scan_state(db_session):
    channel = await channel_service.add_channel(
        db_session, telegram_channel_id=1003, channel_title="Shop"
    )
    now = _now()
    client = FlakyFakeTelegramClient(
        {
            1003: [
                make_message(1, now - timedelta(days=2)),
                make_message(2, now - timedelta(days=1)),
                make_message(3, now),
            ]
        },
        fail_after=1,  # ingest message 1, then blow up before message 2
    )

    result = await scan_channel(
        db_session,
        client,
        channel,
        initial_scan_days=INITIAL_SCAN_DAYS,
        catchup_gap_minutes=CATCHUP_GAP_MINUTES,
    )

    assert not result.ok
    assert result.error is not None

    await db_session.refresh(channel, attribute_names=["scan_state"])
    # The initial scan must NOT be marked complete on failure — the next
    # run has to retry the whole window.
    assert channel.scan_state.initial_scan_completed_at is None
    assert channel.scan_state.scan_phase is ScanPhase.INITIAL
    assert channel.scan_state.consecutive_failures == 1

    error_rows = (
        (await db_session.execute(select(ErrorLog).where(ErrorLog.channel_id == channel.id)))
        .scalars()
        .all()
    )
    assert len(error_rows) == 1
    assert error_rows[0].source == "telegram_monitor"


async def test_scan_recovers_and_completes_after_a_prior_failure(db_session):
    """The message ingested before the earlier failure must not be lost or
    duplicated, and the retry must pick up where it left off."""
    channel = await channel_service.add_channel(
        db_session, telegram_channel_id=1004, channel_title="Shop"
    )
    now = _now()
    messages = {
        1004: [
            make_message(1, now - timedelta(days=2)),
            make_message(2, now - timedelta(days=1)),
        ]
    }

    failing_client = FlakyFakeTelegramClient(messages, fail_after=1)
    first = await scan_channel(
        db_session,
        failing_client,
        channel,
        initial_scan_days=INITIAL_SCAN_DAYS,
        catchup_gap_minutes=CATCHUP_GAP_MINUTES,
    )
    assert not first.ok

    healthy_client = FakeTelegramClient(messages)
    second = await scan_channel(
        db_session,
        healthy_client,
        channel,
        initial_scan_days=INITIAL_SCAN_DAYS,
        catchup_gap_minutes=CATCHUP_GAP_MINUTES,
    )
    assert second.ok
    assert second.phase is ScanPhase.INITIAL  # still catching up on the same window

    await db_session.refresh(channel, attribute_names=["scan_state"])
    assert channel.scan_state.initial_scan_completed_at is not None
    assert channel.scan_state.last_processed_message_id == 2

    # Message 1 (ingested before the failure) must appear exactly once,
    # not duplicated by the retry.
    rows = (
        (
            await db_session.execute(
                select(SourceMessage).where(SourceMessage.channel_id == channel.id)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(r.telegram_message_id for r in rows) == [1, 2]
