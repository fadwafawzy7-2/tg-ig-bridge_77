"""
Tests for `app.queue.scheduler`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable. Covers: daily limits as a strict
MAXIMUM, DB-level duplicate/idempotency safety across a simulated
restart, and timezone-awareness (Asia/Gaza).
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.daily_limit import DailyLimit
from app.models.enums import ContentType, PostPublishStatus, ProductStatus
from app.models.scheduled_post import ScheduledPost
from app.queue.scheduler import build_idempotency_key, run_scheduler
from app.queue.time_utils import today_in_timezone
from tests.factories import make_channel, make_product, make_source_message

pytestmark = pytest.mark.asyncio

GAZA = "Asia/Gaza"


async def _setup(db_session, *, telegram_channel_id: int):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1)
    return channel, message


async def _make_queued_products(db_session, channel, message, *, count: int, prefix: str):
    products = []
    for i in range(count):
        p = await make_product(
            db_session,
            channel,
            message,
            status=ProductStatus.QUEUED,
            content_hash=f"{prefix}-{i}",
            score=100 - i,  # descending scores: first is highest
        )
        products.append(p)
    return products


async def _daily_limit(db_session, *, target_date: date, content_type: ContentType, max_allowed: int):
    limit = DailyLimit(date=target_date, content_type=content_type, max_allowed=max_allowed)
    db_session.add(limit)
    await db_session.flush()
    return limit


# --- Daily limits are a strict MAXIMUM -----------------------------------


async def test_limit_6_with_4_queued_schedules_exactly_4(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=800)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, target_date=target_date, content_type=ContentType.POST, max_allowed=6)
    products = await _make_queued_products(db_session, channel, message, count=4, prefix="p800")

    results = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    scheduled = [r for r in results if r.outcome == "scheduled"]
    assert len(scheduled) == 4  # never invented to fill the remaining 2 slots
    assert {r.product_id for r in scheduled} == {p.id for p in products}

    scheduled_posts = (await db_session.execute(select(ScheduledPost))).scalars().all()
    assert len(scheduled_posts) == 4


async def test_limit_0_schedules_nothing(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=801)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, target_date=target_date, content_type=ContentType.POST, max_allowed=0)
    await _make_queued_products(db_session, channel, message, count=3, prefix="p801")

    results = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    assert all(r.outcome == "limit_reached" for r in results)
    scheduled_posts = (await db_session.execute(select(ScheduledPost))).scalars().all()
    assert scheduled_posts == []


async def test_no_daily_limit_row_schedules_nothing_not_unlimited(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=802)
    target_date = today_in_timezone(GAZA)
    # deliberately do NOT create a DailyLimit row
    await _make_queued_products(db_session, channel, message, count=3, prefix="p802")

    results = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    assert all(r.outcome == "no_daily_limit_configured" for r in results)
    scheduled_posts = (await db_session.execute(select(ScheduledPost))).scalars().all()
    assert scheduled_posts == []


async def test_more_queued_than_capacity_schedules_only_top_scored(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=803)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, target_date=target_date, content_type=ContentType.POST, max_allowed=2)
    products = await _make_queued_products(db_session, channel, message, count=5, prefix="p803")
    # products[0] has the highest score (100), products[4] the lowest (96)

    results = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    scheduled_ids = {r.product_id for r in results if r.outcome == "scheduled"}
    assert scheduled_ids == {products[0].id, products[1].id}
    limit_reached_ids = {r.product_id for r in results if r.outcome == "limit_reached"}
    assert limit_reached_ids == {products[2].id, products[3].id, products[4].id}


async def test_partial_capacity_already_consumed_is_respected(db_session):
    """If 2 posts are already actively scheduled today (e.g. via a manual
    `operations.schedule` call before the batch run), a limit of 3 should
    only allow 1 more, not 3 more."""
    channel, message = await _setup(db_session, telegram_channel_id=804)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, target_date=target_date, content_type=ContentType.POST, max_allowed=3)

    # Pre-consume 2 slots with unrelated already-scheduled posts.
    from app.queue.time_utils import start_of_day_in_timezone

    pre_existing_product_1 = await make_product(
        db_session, channel, message, status=ProductStatus.SCHEDULED, content_hash="pre1"
    )
    pre_existing_product_2 = await make_product(
        db_session, channel, message, status=ProductStatus.SCHEDULED, content_hash="pre2"
    )
    slot_time = start_of_day_in_timezone(target_date, GAZA) + timedelta(hours=11)
    for i, p in enumerate([pre_existing_product_1, pre_existing_product_2]):
        db_session.add(
            ScheduledPost(
                product_id=p.id,
                content_type=ContentType.POST,
                status=PostPublishStatus.SCHEDULED,
                scheduled_for=slot_time + timedelta(minutes=i),
                idempotency_key=f"manual-preexisting-{i}",
            )
        )
    await db_session.commit()

    products = await _make_queued_products(db_session, channel, message, count=3, prefix="p804")
    results = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    scheduled = [r for r in results if r.outcome == "scheduled"]
    assert len(scheduled) == 1
    assert scheduled[0].product_id == products[0].id  # highest score gets the one remaining slot


# --- Duplicate safety / restart idempotency (DB-enforced) ---------------


async def test_rerunning_scheduler_after_status_flip_does_not_duplicate(db_session):
    """Simulates a restart where the product's status was somehow reset
    back to QUEUED after it already got an active scheduled_posts row
    (e.g. a crash between the two writes of a non-atomic caller). The
    scheduler must not create a second row — the DB-level
    idempotency_key/partial-unique-index protection must catch it."""
    channel, message = await _setup(db_session, telegram_channel_id=805)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, target_date=target_date, content_type=ContentType.POST, max_allowed=5)
    products = await _make_queued_products(db_session, channel, message, count=1, prefix="p805")
    product = products[0]

    first_run = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)
    assert first_run[0].outcome == "scheduled"

    # Simulate the restart: manually put the product back to QUEUED
    # without touching the already-created scheduled_posts row.
    await db_session.refresh(product)
    product.status = ProductStatus.QUEUED
    await db_session.commit()

    second_run = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    assert second_run[0].outcome == "already_scheduled"
    all_posts = (
        (await db_session.execute(select(ScheduledPost).where(ScheduledPost.product_id == product.id)))
        .scalars()
        .all()
    )
    assert len(all_posts) == 1  # still only one row - no duplicate was created


async def test_idempotency_key_is_deterministic_and_reproducible(db_session):
    key1 = build_idempotency_key(
        product_id=42, content_type=ContentType.POST, target_date=date(2026, 9, 8)
    )
    key2 = build_idempotency_key(
        product_id=42, content_type=ContentType.POST, target_date=date(2026, 9, 8)
    )
    key3 = build_idempotency_key(
        product_id=42, content_type=ContentType.REEL, target_date=date(2026, 9, 8)
    )
    assert key1 == key2
    assert key1 != key3


# --- Timezone awareness (Asia/Gaza) ---------------------------------------


async def test_scheduled_for_is_timezone_aware_in_gaza_offset(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=806)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, target_date=target_date, content_type=ContentType.POST, max_allowed=2)
    await _make_queued_products(db_session, channel, message, count=1, prefix="p806")

    results = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    scheduled_for = results[0].scheduled_for
    assert scheduled_for is not None
    assert scheduled_for.tzinfo is not None
    expected_offset = ZoneInfo(GAZA).utcoffset(scheduled_for)
    assert scheduled_for.utcoffset() == expected_offset


async def test_capacity_counting_uses_gaza_day_boundary_not_utc(db_session):
    """A scheduled_posts row whose UTC instant falls on one UTC calendar
    day but on the NEXT Gaza calendar day (Gaza is UTC+2/+3, ahead of
    UTC) must be counted against the Gaza date, not the UTC date."""
    channel, message = await _setup(db_session, telegram_channel_id=807)
    target_date = today_in_timezone(GAZA)
    tomorrow_gaza = target_date + timedelta(days=1)
    await _daily_limit(
        db_session, target_date=tomorrow_gaza, content_type=ContentType.POST, max_allowed=1
    )

    from app.queue.time_utils import start_of_day_in_timezone

    # 00:30 Gaza time on `tomorrow_gaza` — this instant, converted to UTC,
    # is still `target_date` in UTC (since Gaza is ahead of UTC), but must
    # count against `tomorrow_gaza`'s capacity, not today's.
    edge_time = start_of_day_in_timezone(tomorrow_gaza, GAZA) + timedelta(minutes=30)
    assert edge_time.astimezone(ZoneInfo("UTC")).date() == target_date  # sanity check on the edge case

    pre_existing = await make_product(
        db_session, channel, message, status=ProductStatus.SCHEDULED, content_hash="edge-pre"
    )
    db_session.add(
        ScheduledPost(
            product_id=pre_existing.id,
            content_type=ContentType.POST,
            status=PostPublishStatus.SCHEDULED,
            scheduled_for=edge_time,
            idempotency_key="edge-case-pre-existing",
        )
    )
    await db_session.commit()

    await _make_queued_products(db_session, channel, message, count=1, prefix="p807")
    results = await run_scheduler(
        db_session, content_type=ContentType.POST, target_date=tomorrow_gaza
    )

    # Capacity for tomorrow_gaza (max_allowed=1) was already consumed by
    # the edge-time row, so the new product must NOT get scheduled.
    assert results[0].outcome == "limit_reached"


# --- State transitions ---------------------------------------------------


async def test_scheduled_product_gets_a_scheduled_post_row_with_selected_caption(db_session):
    from tests.factories import make_caption

    channel, message = await _setup(db_session, telegram_channel_id=808)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, target_date=target_date, content_type=ContentType.POST, max_allowed=1)
    product = (await _make_queued_products(db_session, channel, message, count=1, prefix="p808"))[0]
    caption = await make_caption(db_session, product, text="Selected caption text")

    results = await run_scheduler(db_session, content_type=ContentType.POST, target_date=target_date)

    assert results[0].outcome == "scheduled"
    scheduled_post = await db_session.get(ScheduledPost, results[0].scheduled_post_id)
    assert scheduled_post.status == PostPublishStatus.SCHEDULED
    assert scheduled_post.caption_id == caption.id
    assert scheduled_post.content_type == ContentType.POST

    await db_session.refresh(product)
    assert product.status == ProductStatus.SCHEDULED
