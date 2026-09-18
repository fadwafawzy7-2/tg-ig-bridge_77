"""
Tests for `app.queue.runner.run_pipeline` — the full
VALIDATED -> ELIGIBLE -> QUEUED -> SCHEDULED stitch.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

import pytest
from sqlalchemy import select

from app.models.daily_limit import DailyLimit
from app.models.enums import ContentType, ProductStatus
from app.models.scheduled_post import ScheduledPost
from app.queue.runner import run_pipeline
from app.queue.time_utils import today_in_timezone
from tests.factories import make_caption, make_channel, make_media, make_product, make_source_message

pytestmark = pytest.mark.asyncio

GAZA = "Asia/Gaza"


async def _setup(db_session, *, telegram_channel_id: int):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1)
    return channel, message


async def _daily_limit(db_session, *, max_allowed: int):
    limit = DailyLimit(
        date=today_in_timezone(GAZA), content_type=ContentType.POST, max_allowed=max_allowed
    )
    db_session.add(limit)
    await db_session.flush()
    return limit


async def test_fully_qualified_product_goes_all_the_way_to_scheduled(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=1000)
    await _daily_limit(db_session, max_allowed=5)
    product = await make_product(db_session, channel, message, status=ProductStatus.VALIDATED)
    await make_caption(db_session, product)
    await make_media(db_session, message, product=product)

    summary = await run_pipeline(db_session)

    assert summary.newly_eligible == 1
    assert summary.newly_queued == 1
    assert summary.newly_scheduled == 1

    await db_session.refresh(product)
    assert product.status == ProductStatus.SCHEDULED

    scheduled_posts = (
        (await db_session.execute(select(ScheduledPost).where(ScheduledPost.product_id == product.id)))
        .scalars()
        .all()
    )
    assert len(scheduled_posts) == 1


async def test_product_missing_media_never_leaves_validated(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=1001)
    await _daily_limit(db_session, max_allowed=5)
    product = await make_product(db_session, channel, message, status=ProductStatus.VALIDATED)
    await make_caption(db_session, product)
    # no media created

    summary = await run_pipeline(db_session)

    assert summary.newly_eligible == 0
    assert summary.newly_queued == 0
    assert summary.newly_scheduled == 0

    await db_session.refresh(product)
    assert product.status == ProductStatus.VALIDATED


async def test_daily_limit_carries_through_the_whole_pipeline(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=1002)
    await _daily_limit(db_session, max_allowed=2)

    products = []
    for i in range(4):
        p = await make_product(
            db_session,
            channel,
            message,
            status=ProductStatus.VALIDATED,
            content_hash=f"h{i}",
            score=None,
        )
        await make_caption(db_session, p)
        await make_media(db_session, message, product=p, telegram_file_id=f"file-{i}")
        products.append(p)

    summary = await run_pipeline(db_session)

    assert summary.newly_eligible == 4  # all 4 are eligible...
    assert summary.newly_queued == 4  # ...and all 4 get queued...
    assert summary.newly_scheduled == 2  # ...but only 2 (the daily max) get scheduled

    statuses = []
    for p in products:
        await db_session.refresh(p)
        statuses.append(p.status)
    assert statuses.count(ProductStatus.SCHEDULED) == 2
    assert statuses.count(ProductStatus.QUEUED) == 2


async def test_running_pipeline_twice_is_idempotent(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=1003)
    await _daily_limit(db_session, max_allowed=5)
    product = await make_product(db_session, channel, message, status=ProductStatus.VALIDATED)
    await make_caption(db_session, product)
    await make_media(db_session, message, product=product)

    first = await run_pipeline(db_session)
    second = await run_pipeline(db_session)

    assert first.newly_scheduled == 1
    assert second.newly_eligible == 0
    assert second.newly_queued == 0
    assert second.newly_scheduled == 0

    scheduled_posts = (
        (await db_session.execute(select(ScheduledPost).where(ScheduledPost.product_id == product.id)))
        .scalars()
        .all()
    )
    assert len(scheduled_posts) == 1  # still just one row
