"""
Tests for dynamic Telegram channel management
(`app.telegram.channel_service`).

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

import pytest

from app.core.exceptions import NotFoundError
from app.models.enums import ChannelStatus
from app.telegram import channel_service

pytestmark = pytest.mark.asyncio


async def test_add_channel_creates_channel_and_scan_state(db_session):
    channel = await channel_service.add_channel(
        db_session,
        telegram_channel_id=111,
        channel_title="Deals Channel",
        channel_username="deals",
    )

    assert channel.id is not None
    assert channel.status == ChannelStatus.ACTIVE
    # A fresh channel must start with a scan_state row so the very next
    # scan has something to read (and defaults to phase=INITIAL).
    await db_session.refresh(channel, attribute_names=["scan_state"])
    assert channel.scan_state is not None
    assert channel.scan_state.initial_scan_completed_at is None


async def test_add_channel_is_idempotent(db_session):
    first = await channel_service.add_channel(
        db_session, telegram_channel_id=222, channel_title="Original Title"
    )
    second = await channel_service.add_channel(
        db_session, telegram_channel_id=222, channel_title="Renamed Title"
    )

    assert first.id == second.id
    assert second.channel_title == "Renamed Title"

    all_channels = await channel_service.list_channels(db_session)
    matching = [c for c in all_channels if c.telegram_channel_id == 222]
    assert len(matching) == 1


async def test_disable_then_add_reactivates_without_losing_scan_state(db_session):
    channel = await channel_service.add_channel(
        db_session, telegram_channel_id=333, channel_title="Reactivate Me"
    )
    await db_session.refresh(channel, attribute_names=["scan_state"])
    scan_state_id_before = channel.scan_state.id

    await channel_service.disable_channel(db_session, channel.id)

    reactivated = await channel_service.add_channel(
        db_session, telegram_channel_id=333, channel_title="Reactivate Me"
    )
    assert reactivated.status == ChannelStatus.ACTIVE
    await db_session.refresh(reactivated, attribute_names=["scan_state"])
    # Same scan_state row survives disable -> re-add: re-enabling a channel
    # must resume monitoring, not restart the initial 5-day scan.
    assert reactivated.scan_state.id == scan_state_id_before


async def test_disable_and_enable_toggle_status(db_session):
    channel = await channel_service.add_channel(
        db_session, telegram_channel_id=444, channel_title="Toggle Me"
    )

    disabled = await channel_service.disable_channel(db_session, channel.id)
    assert disabled.status == ChannelStatus.DISABLED

    enabled = await channel_service.enable_channel(db_session, channel.id)
    assert enabled.status == ChannelStatus.ACTIVE


async def test_list_channels_filters_by_status(db_session):
    active = await channel_service.add_channel(
        db_session, telegram_channel_id=555, channel_title="Active One"
    )
    to_disable = await channel_service.add_channel(
        db_session, telegram_channel_id=666, channel_title="Disabled One"
    )
    await channel_service.disable_channel(db_session, to_disable.id)

    active_channels = await channel_service.list_channels(db_session, status=ChannelStatus.ACTIVE)
    active_ids = {c.id for c in active_channels}
    assert active.id in active_ids
    assert to_disable.id not in active_ids


async def test_get_channel_raises_not_found_for_missing_id(db_session):
    with pytest.raises(NotFoundError):
        await channel_service.get_channel(db_session, 999_999)
