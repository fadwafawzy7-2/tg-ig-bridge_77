"""
Tests for `app.instagram.runner.run_publisher` — AUTO vs REVIEW mode.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.instagram.runner import run_publisher
from app.models.enums import ContentType, PostPublishStatus, ProductStatus
from app.models.published_post import PublishedPost
from app.queue.time_utils import utcnow
from tests.factories import (
    make_caption,
    make_channel,
    make_daily_limit,
    make_media,
    make_product,
    make_scheduled_post,
    make_source_message,
)
from tests.fakes import FakeInstagramClient

pytestmark = pytest.mark.asyncio

GAZA = "Asia/Gaza"


async def _setup_due_scheduled_post(db_session, *, telegram_channel_id: int):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1, raw_text="Nike Air Max\n30 KWD")
    product = await make_product(db_session, channel, message, status=ProductStatus.SCHEDULED, price=Decimal("30"), currency="KWD")
    caption = await make_caption(db_session, product, text="Nike Air Max available now, message us for details!")
    await make_media(db_session, message, product=product)
    scheduled_post = await make_scheduled_post(
        db_session, product, caption=caption, scheduled_for=utcnow() - timedelta(minutes=5)
    )
    tz = ZoneInfo(GAZA)
    target_date = scheduled_post.scheduled_for.astimezone(tz).date()
    await make_daily_limit(db_session, date=target_date, content_type=ContentType.POST, max_allowed=5)
    return product, scheduled_post


def _with_mode(mode: str):
    from app.core.config import get_settings

    get_settings.cache_clear()
    return patch.dict("os.environ", {"INSTAGRAM_PUBLISH_MODE": mode})


async def test_review_mode_does_not_publish_due_items(db_session):
    product, scheduled_post = await _setup_due_scheduled_post(db_session, telegram_channel_id=1300)
    client = FakeInstagramClient()

    with _with_mode("REVIEW"):
        summary = await run_publisher(db_session, client)

    assert summary.mode == "REVIEW"
    assert summary.published == 0
    assert summary.due_but_awaiting_review == 1
    assert client.created_containers == []

    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.SCHEDULED

    published_posts = (await db_session.execute(select(PublishedPost))).scalars().all()
    assert published_posts == []


async def test_auto_mode_publishes_due_items(db_session):
    product, scheduled_post = await _setup_due_scheduled_post(db_session, telegram_channel_id=1301)
    client = FakeInstagramClient()

    with _with_mode("AUTO"):
        summary = await run_publisher(db_session, client)

    assert summary.mode == "AUTO"
    assert summary.published == 1
    assert summary.due_but_awaiting_review == 0

    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.PUBLISHED

    published_posts = (await db_session.execute(select(PublishedPost))).scalars().all()
    assert len(published_posts) == 1


async def test_auto_mode_ignores_not_yet_due_items(db_session):
    channel = await make_channel(db_session, telegram_channel_id=1302)
    message = await make_source_message(db_session, channel, telegram_message_id=1, raw_text="Nike Air Max\n30 KWD")
    product = await make_product(db_session, channel, message, status=ProductStatus.SCHEDULED, price=Decimal("30"), currency="KWD")
    caption = await make_caption(db_session, product, text="Nike Air Max available now, message us for details!")
    await make_media(db_session, message, product=product)
    future_post = await make_scheduled_post(
        db_session, product, caption=caption, scheduled_for=utcnow() + timedelta(hours=5)
    )
    tz = ZoneInfo(GAZA)
    await make_daily_limit(
        db_session, date=future_post.scheduled_for.astimezone(tz).date(), content_type=ContentType.POST, max_allowed=5
    )

    client = FakeInstagramClient()
    with _with_mode("AUTO"):
        summary = await run_publisher(db_session, client)

    assert summary.published == 0
    await db_session.refresh(future_post)
    assert future_post.status == PostPublishStatus.SCHEDULED


async def test_default_mode_is_review(db_session):
    """No explicit INSTAGRAM_PUBLISH_MODE env var set -> defaults to
    REVIEW (nothing auto-published) per the Settings default."""
    product, scheduled_post = await _setup_due_scheduled_post(db_session, telegram_channel_id=1303)
    client = FakeInstagramClient()

    from app.core.config import get_settings

    get_settings.cache_clear()
    summary = await run_publisher(db_session, client)

    assert summary.mode == "REVIEW"
    assert summary.published == 0
