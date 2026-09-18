"""
Tests for `app.instagram.publisher_service`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable. Uses `tests.fakes.FakeInstagramClient`
— no network, no real Graph API — per the requirement to mock the
Instagram API in tests.

Covers: image publication, Reel publication, carousel, container
processing/polling, API failures (auth/media/rate-limit), retry with
backoff, timeout, duplicate protection, restart safety, idempotency,
invalid/unvalidated caption, merchant price protection, daily limit = 0,
daily limit partially consumed, and concurrent publishing not exceeding
the limit.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.instagram.client import (
    InstagramAuthError,
    InstagramMediaError,
    InstagramRateLimitError,
)
from app.instagram.publisher_service import publish_scheduled_post
from app.models.daily_limit import DailyLimit
from app.models.enums import CaptionValidationStatus, ContentType, MediaType, PostPublishStatus, ProductStatus
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
from tests.fakes import ErrorContainerFakeInstagramClient, FakeInstagramClient

pytestmark = pytest.mark.asyncio

GAZA = "Asia/Gaza"


async def _setup_ready_product(
    db_session,
    *,
    telegram_channel_id: int,
    content_type: ContentType = ContentType.POST,
    media_type: MediaType = MediaType.PHOTO,
    media_count: int = 1,
    raw_text: str = "Nike Air Max\n30 KWD",
    caption_text: str = "Nike Air Max available now, message us for details!",
    price=Decimal("30"),
    currency="KWD",
    scheduled_for=None,
):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1, raw_text=raw_text)
    product = await make_product(
        db_session, channel, message, status=ProductStatus.SCHEDULED, price=price, currency=currency
    )
    caption = await make_caption(db_session, product, text=caption_text)
    for i in range(media_count):
        await make_media(db_session, message, product=product, media_type=media_type, telegram_file_id=f"file-{i}")
    scheduled_post = await make_scheduled_post(
        db_session,
        product,
        content_type=content_type,
        caption=caption,
        scheduled_for=scheduled_for or utcnow(),
    )
    return product, caption, scheduled_post


async def _daily_limit_for(db_session, scheduled_post, *, max_allowed: int, published_count: int = 0):
    tz = ZoneInfo(GAZA)
    target_date = scheduled_post.scheduled_for.astimezone(tz).date()
    return await make_daily_limit(
        db_session,
        date=target_date,
        content_type=scheduled_post.content_type,
        max_allowed=max_allowed,
        published_count=published_count,
    )


# --- Image publication ----------------------------------------------


async def test_image_post_publishes_successfully(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1200)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "published"
    assert outcome.instagram_media_id is not None

    await db_session.refresh(scheduled_post)
    await db_session.refresh(product)
    assert scheduled_post.status == PostPublishStatus.PUBLISHED
    assert product.status == ProductStatus.PUBLISHED

    published_post = (
        await db_session.execute(
            select(PublishedPost).where(PublishedPost.scheduled_post_id == scheduled_post.id)
        )
    ).scalars().first()
    assert published_post is not None
    assert published_post.instagram_media_id == outcome.instagram_media_id
    assert published_post.published_at is not None
    assert published_post.content_type == ContentType.POST
    assert published_post.caption_id == caption.id

    assert len(client.created_containers) == 1
    assert client.created_containers[0]["media_type"] is None


async def test_carousel_post_creates_children_and_parent_container(db_session):
    product, caption, scheduled_post = await _setup_ready_product(
        db_session, telegram_channel_id=1201, media_count=3
    )
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "published"
    assert len(client.created_containers) == 4
    children = [c for c in client.created_containers if c["is_carousel_item"]]
    parent = [c for c in client.created_containers if c["media_type"] == "CAROUSEL"]
    assert len(children) == 3
    assert len(parent) == 1
    assert set(parent[0]["children"]) == {c["id"] for c in children}


# --- Reel publication --------------------------------------------------


async def test_reel_publishes_successfully(db_session):
    product, caption, scheduled_post = await _setup_ready_product(
        db_session, telegram_channel_id=1202, content_type=ContentType.REEL, media_type=MediaType.VIDEO
    )
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5, published_count=0)
    client = FakeInstagramClient()

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "published"
    assert client.created_containers[0]["media_type"] == "REELS"

    published_post = (
        await db_session.execute(
            select(PublishedPost).where(PublishedPost.scheduled_post_id == scheduled_post.id)
        )
    ).scalars().first()
    assert published_post.content_type == ContentType.REEL


async def test_reel_with_no_valid_video_media_fails_permanently(db_session):
    product, caption, scheduled_post = await _setup_ready_product(
        db_session, telegram_channel_id=1203, content_type=ContentType.REEL, media_type=MediaType.PHOTO
    )
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "unsafe"
    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.FAILED


# --- Container processing / polling -------------------------------------


async def test_container_stuck_in_progress_times_out_and_retries(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1204)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()
    client.stuck_in_progress_containers.add("container-1")

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "retrying"
    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.SCHEDULED
    assert scheduled_post.attempt_count == 1
    assert scheduled_post.last_error is not None

    limit = (await db_session.execute(select(DailyLimit))).scalars().first()
    assert limit.published_count == 0


async def test_container_error_status_fails_permanently(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1205)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = ErrorContainerFakeInstagramClient()

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "failed_permanent"
    await db_session.refresh(scheduled_post)
    await db_session.refresh(product)
    assert scheduled_post.status == PostPublishStatus.FAILED
    assert product.status == ProductStatus.FAILED

    limit = (await db_session.execute(select(DailyLimit))).scalars().first()
    assert limit.published_count == 0


# --- API failures + retry classification -------------------------------


async def test_auth_error_is_permanent_not_retried(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1206)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()
    client.fail_with = InstagramAuthError("bad token")

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "failed_permanent"
    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.FAILED


async def test_media_error_is_permanent_not_retried(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1207)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()
    client.fail_with = InstagramMediaError("unsupported media format")

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "failed_permanent"


async def test_rate_limit_error_is_transient_and_retried(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1208)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()
    client.fail_with = InstagramRateLimitError("rate limited")

    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "retrying"
    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.SCHEDULED
    assert scheduled_post.attempt_count == 1


async def test_retry_eventually_succeeds_after_transient_failure(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1209)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()
    client.fail_with = InstagramRateLimitError("rate limited")

    first = await publish_scheduled_post(db_session, scheduled_post.id, client)
    assert first.outcome == "retrying"

    await db_session.refresh(scheduled_post)
    scheduled_post.last_attempt_at = utcnow() - timedelta(seconds=9999)
    await db_session.commit()

    second = await publish_scheduled_post(db_session, scheduled_post.id, client)
    assert second.outcome == "published"


async def test_not_due_for_retry_is_skipped(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1210)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()
    client.fail_with = InstagramRateLimitError("rate limited")

    first = await publish_scheduled_post(db_session, scheduled_post.id, client)
    assert first.outcome == "retrying"

    second = await publish_scheduled_post(db_session, scheduled_post.id, client)
    assert second.outcome == "not_due_for_retry"


async def test_max_attempts_exceeded_fails_permanently(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1211)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)

    from app.core.config import get_settings

    settings = get_settings()
    scheduled_post.attempt_count = settings.INSTAGRAM_MAX_PUBLISH_ATTEMPTS
    scheduled_post.last_attempt_at = utcnow() - timedelta(hours=1)
    await db_session.commit()

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "failed_permanent"
    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.FAILED
    assert client.created_containers == []


# --- Duplicate protection / restart safety / idempotency --------------


async def test_already_published_is_idempotent_no_op(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1212)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    client = FakeInstagramClient()

    first = await publish_scheduled_post(db_session, scheduled_post.id, client)
    assert first.outcome == "published"

    second = await publish_scheduled_post(db_session, scheduled_post.id, client)
    assert second.outcome == "already_published"

    published_posts = (
        (await db_session.execute(select(PublishedPost).where(PublishedPost.scheduled_post_id == scheduled_post.id)))
        .scalars()
        .all()
    )
    assert len(published_posts) == 1

    limit = (await db_session.execute(select(DailyLimit))).scalars().first()
    assert limit.published_count == 1

    assert len(client.published_container_ids) == 1


async def test_restart_mid_processing_does_not_reclaim_or_duplicate(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1213)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)
    scheduled_post.status = PostPublishStatus.PROCESSING
    await db_session.commit()

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "already_claimed"
    assert client.created_containers == []


async def test_cancelled_scheduled_post_is_never_published(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1214)
    scheduled_post.status = PostPublishStatus.CANCELLED
    await db_session.commit()

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "cancelled"
    assert client.created_containers == []


# --- Invalid/unvalidated caption ----------------------------------------


async def test_unvalidated_caption_is_never_published(db_session):
    channel = await make_channel(db_session, telegram_channel_id=1215)
    message = await make_source_message(db_session, channel, telegram_message_id=1, raw_text="Nike Air Max\n30 KWD")
    product = await make_product(db_session, channel, message, status=ProductStatus.SCHEDULED)
    caption = await make_caption(
        db_session,
        product,
        text="Nike Air Max, message us!",
        is_selected=False,
        validation_status=CaptionValidationStatus.REJECTED,
        rejection_reason="test rejection",
    )
    await make_media(db_session, message, product=product)
    scheduled_post = await make_scheduled_post(db_session, product, caption=caption)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "unsafe"
    assert client.created_containers == []
    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.FAILED


async def test_scheduled_post_with_no_caption_at_all_is_unsafe(db_session):
    channel = await make_channel(db_session, telegram_channel_id=1216)
    message = await make_source_message(db_session, channel, telegram_message_id=1)
    product = await make_product(db_session, channel, message, status=ProductStatus.SCHEDULED)
    await make_media(db_session, message, product=product)
    scheduled_post = await make_scheduled_post(db_session, product, caption=None)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "unsafe"


# --- Merchant price protection ------------------------------------------


async def test_merchant_price_in_caption_blocks_publish(db_session):
    product, caption, scheduled_post = await _setup_ready_product(
        db_session,
        telegram_channel_id=1217,
        caption_text="Nike Air Max only 30 KWD, grab yours now!",
    )
    await _daily_limit_for(db_session, scheduled_post, max_allowed=5)

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "unsafe"
    assert "re-validation failed" in outcome.error
    assert client.created_containers == []

    published_posts = (await db_session.execute(select(PublishedPost))).scalars().all()
    assert published_posts == []


# --- Daily limit = 0 / partially consumed --------------------------------


async def test_daily_limit_zero_blocks_publish_entirely(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1218)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=0)

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "limit_reached"
    assert client.created_containers == []
    await db_session.refresh(scheduled_post)
    assert scheduled_post.status == PostPublishStatus.SCHEDULED

    published_posts = (await db_session.execute(select(PublishedPost))).scalars().all()
    assert published_posts == []


async def test_daily_limit_partially_consumed_allows_remaining_slot(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1219)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=2, published_count=1)

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "published"
    limit = (await db_session.execute(select(DailyLimit))).scalars().first()
    assert limit.published_count == 2


async def test_daily_limit_fully_consumed_blocks_further_publishing(db_session):
    product, caption, scheduled_post = await _setup_ready_product(db_session, telegram_channel_id=1220)
    await _daily_limit_for(db_session, scheduled_post, max_allowed=2, published_count=2)

    client = FakeInstagramClient()
    outcome = await publish_scheduled_post(db_session, scheduled_post.id, client)

    assert outcome.outcome == "limit_reached"


# --- Concurrent publishing across two DIFFERENT scheduled posts --------


async def test_concurrent_publishing_of_two_products_never_exceeds_limit(db_session):
    product1, caption1, sp1 = await _setup_ready_product(db_session, telegram_channel_id=1221)
    product2, caption2, sp2 = await _setup_ready_product(db_session, telegram_channel_id=1222)
    await _daily_limit_for(db_session, sp1, max_allowed=1, published_count=0)

    async def _attempt(scheduled_post_id):
        async with AsyncSessionLocal() as session:
            return await publish_scheduled_post(session, scheduled_post_id, FakeInstagramClient())

    outcome1, outcome2 = await asyncio.gather(_attempt(sp1.id), _attempt(sp2.id))

    outcomes = sorted([outcome1.outcome, outcome2.outcome])
    assert outcomes == ["limit_reached", "published"]

    limit = (await db_session.execute(select(DailyLimit))).scalars().first()
    assert limit.published_count == 1

    published_posts = (await db_session.execute(select(PublishedPost))).scalars().all()
    assert len(published_posts) == 1
