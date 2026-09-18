"""
Tests for `app.analytics.analytics_service`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable. Covers: real-data-only loading,
Asia/Gaza timezone conversion, latest-snapshot-per-post dedup, content
type filtering, date range filtering, insufficient-data fallback, and
"learning" as more real data accumulates (end-to-end through the DB, not
just the pure engine).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.analytics.analytics_service import get_best_times, get_performance_breakdown
from app.models.enums import ContentType, ProductStatus, StoryStatus
from app.queue.scheduler import SCHEDULING_WINDOW_END_HOUR, SCHEDULING_WINDOW_START_HOUR
from tests.factories import (
    make_analytics,
    make_channel,
    make_product,
    make_published_post,
    make_source_message,
    make_story,
)

pytestmark = pytest.mark.asyncio

GAZA = ZoneInfo("Asia/Gaza")


async def _setup(db_session, *, telegram_channel_id: int):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1)
    product = await make_product(db_session, channel, message, status=ProductStatus.PUBLISHED)
    return channel, message, product


def _gaza_dt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=GAZA)


# --- Insufficient-data fallback (end-to-end) -----------------------------


async def test_no_data_at_all_falls_back_to_default_window(db_session):
    result = await get_best_times(db_session)
    assert result.is_learned is False
    assert result.fallback_window == (SCHEDULING_WINDOW_START_HOUR, SCHEDULING_WINDOW_END_HOUR)
    assert result.best_hour is None


async def test_a_few_posts_with_no_analytics_still_falls_back(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1100)
    for i in range(3):
        await make_published_post(
            db_session, product, published_at=_gaza_dt(2026, 9, 1, 10 + i)
        )
    # no Analytics rows created at all -> zero usable engagement samples
    result = await get_best_times(db_session)
    assert result.is_learned is False
    assert result.sample_count == 0


# --- Real-data-only, never invented ---------------------------------------


async def test_posts_without_any_usable_metric_are_excluded(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1101)
    post = await make_published_post(db_session, product, published_at=_gaza_dt(2026, 9, 1, 10))
    # analytics row exists but has NO usable numbers (no rate, no reach/impressions)
    await make_analytics(db_session, published_post=post, metric_date=datetime(2026, 9, 2).date())

    breakdown = await get_performance_breakdown(db_session)
    assert breakdown.total_samples == 0


async def test_posts_with_real_engagement_rate_are_counted(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1102)
    post = await make_published_post(db_session, product, published_at=_gaza_dt(2026, 9, 1, 10))
    await make_analytics(
        db_session,
        published_post=post,
        metric_date=datetime(2026, 9, 2).date(),
        engagement_rate=Decimal("0.045"),
    )

    breakdown = await get_performance_breakdown(db_session)
    assert breakdown.total_samples == 1
    assert breakdown.by_hour[0].key == 10
    assert breakdown.by_hour[0].mean_engagement == Decimal("0.045")


# --- Latest-snapshot-only dedup -------------------------------------------


async def test_only_latest_metric_date_snapshot_is_used_per_post(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1103)
    post = await make_published_post(db_session, product, published_at=_gaza_dt(2026, 9, 1, 10))
    await make_analytics(
        db_session,
        published_post=post,
        metric_date=datetime(2026, 9, 2).date(),
        engagement_rate=Decimal("0.01"),  # older, smaller
    )
    await make_analytics(
        db_session,
        published_post=post,
        metric_date=datetime(2026, 9, 5).date(),
        engagement_rate=Decimal("0.09"),  # newer, larger - this one should win
    )

    breakdown = await get_performance_breakdown(db_session)
    assert breakdown.total_samples == 1  # NOT 2 - only the latest snapshot counted
    assert breakdown.by_hour[0].mean_engagement == Decimal("0.09")


# --- Content type filtering ------------------------------------------------


async def test_content_type_filter_only_returns_that_type(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1104)
    post = await make_published_post(
        db_session, product, content_type=ContentType.POST, published_at=_gaza_dt(2026, 9, 1, 10)
    )
    reel = await make_published_post(
        db_session, product, content_type=ContentType.REEL, published_at=_gaza_dt(2026, 9, 1, 11)
    )
    await make_analytics(db_session, published_post=post, engagement_rate=Decimal("0.05"))
    await make_analytics(db_session, published_post=reel, engagement_rate=Decimal("0.08"))

    post_only = await get_performance_breakdown(db_session, content_type=ContentType.POST)
    assert post_only.total_samples == 1
    assert post_only.by_content_type[0].key == "POST"

    reel_only = await get_performance_breakdown(db_session, content_type=ContentType.REEL)
    assert reel_only.total_samples == 1
    assert reel_only.by_content_type[0].key == "REEL"


async def test_story_content_and_status_are_respected(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1105)
    published_story = await make_story(
        db_session, product, status=StoryStatus.PUBLISHED, published_at=_gaza_dt(2026, 9, 1, 20)
    )
    await make_analytics(db_session, story=published_story, engagement_rate=Decimal("0.02"))

    # a PENDING story with no published_at must never contribute, even if
    # (unrealistically) it had an analytics row attached.
    pending_story = await make_story(
        db_session, product, status=StoryStatus.PENDING, published_at=None
    )
    await make_analytics(db_session, story=pending_story, engagement_rate=Decimal("0.99"))

    breakdown = await get_performance_breakdown(db_session, content_type=ContentType.STORY)
    assert breakdown.total_samples == 1
    assert breakdown.by_hour[0].mean_engagement == Decimal("0.02")


async def test_get_best_times_content_type_filter_excludes_stories_from_post_query(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1106)
    story = await make_story(db_session, product, published_at=_gaza_dt(2026, 9, 1, 10))
    await make_analytics(db_session, story=story, engagement_rate=Decimal("0.5"))

    breakdown = await get_performance_breakdown(db_session, content_type=ContentType.POST)
    assert breakdown.total_samples == 0


# --- Date range filtering ---------------------------------------------


async def test_since_until_filters_restrict_to_date_range(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1107)
    early = await make_published_post(db_session, product, published_at=_gaza_dt(2026, 1, 1, 10))
    in_range = await make_published_post(db_session, product, published_at=_gaza_dt(2026, 6, 1, 10))
    late = await make_published_post(db_session, product, published_at=_gaza_dt(2026, 12, 1, 10))
    for p in (early, in_range, late):
        await make_analytics(db_session, published_post=p, engagement_rate=Decimal("0.05"))

    breakdown = await get_performance_breakdown(
        db_session, since=datetime(2026, 3, 1).date(), until=datetime(2026, 9, 1).date()
    )
    assert breakdown.total_samples == 1


# --- Timezone correctness (Asia/Gaza) -------------------------------------


async def test_published_at_is_converted_to_gaza_local_hour(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1108)
    # 22:30 UTC -> Gaza is UTC+3 in this period -> 01:30 next day Gaza time (hour=1)
    utc_time = datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)
    post = await make_published_post(db_session, product, published_at=utc_time)
    await make_analytics(db_session, published_post=post, engagement_rate=Decimal("0.05"))

    expected_local_hour = utc_time.astimezone(GAZA).hour

    breakdown = await get_performance_breakdown(db_session)
    assert breakdown.total_samples == 1
    assert breakdown.by_hour[0].key == expected_local_hour


# --- Ranking + learning as data accumulates (end-to-end) -----------------


async def test_best_times_ranks_by_real_engagement_and_learns_as_data_grows(db_session):
    channel, message, product = await _setup(db_session, telegram_channel_id=1109)

    # Stage 1: only 2 posts - not enough for a learned recommendation.
    for i in range(2):
        post = await make_published_post(
            db_session, product, published_at=_gaza_dt(2026, 9, 1, 10, minute=i)
        )
        await make_analytics(db_session, published_post=post, engagement_rate=Decimal("0.10"))

    result_stage1 = await get_best_times(db_session)
    assert result_stage1.is_learned is False

    # Stage 2: enough real posts now, hour 10 has the strongest engagement.
    for i in range(3):
        post = await make_published_post(
            db_session, product, published_at=_gaza_dt(2026, 9, 2, 10, minute=i)
        )
        await make_analytics(db_session, published_post=post, engagement_rate=Decimal("0.30"))
    for i in range(3):
        post = await make_published_post(
            db_session, product, published_at=_gaza_dt(2026, 9, 2, 18, minute=i)
        )
        await make_analytics(db_session, published_post=post, engagement_rate=Decimal("0.05"))

    result_stage2 = await get_best_times(db_session)
    assert result_stage2.is_learned is True
    assert result_stage2.best_hour == 10
    assert result_stage2.sample_count == 8  # 2 + 3 + 3


async def test_best_times_notes_never_claim_proven_when_falling_back(db_session):
    result = await get_best_times(db_session)
    assert result.is_learned is False
    lowered = result.notes.lower()
    assert "fallback" in lowered or "instead" in lowered or "default" in lowered
