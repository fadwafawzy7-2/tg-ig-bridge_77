"""
Tests for `app.queue.operations` — the five domain operations: schedule,
publish_now, skip, retry_failed, reprioritize.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.exceptions import ValidationError
from app.models.daily_limit import DailyLimit
from app.models.enums import ContentType, PostPublishStatus, ProductStatus
from app.models.scheduled_post import ScheduledPost
from app.queue import operations
from app.queue.scheduler import build_idempotency_key
from app.queue.time_utils import now_in_timezone, today_in_timezone
from tests.factories import make_channel, make_product, make_source_message

pytestmark = pytest.mark.asyncio

GAZA = "Asia/Gaza"


async def _setup(db_session, *, telegram_channel_id: int):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1)
    return channel, message


async def _daily_limit(db_session, *, content_type=ContentType.POST, max_allowed: int):
    limit = DailyLimit(date=today_in_timezone(GAZA), content_type=content_type, max_allowed=max_allowed)
    db_session.add(limit)
    await db_session.flush()
    return limit


# --- schedule ------------------------------------------------------------


async def test_schedule_eligible_product_succeeds(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=900)
    await _daily_limit(db_session, max_allowed=5)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    result = await operations.schedule(db_session, product)

    assert result.outcome == "scheduled"
    await db_session.refresh(product)
    assert product.status == ProductStatus.SCHEDULED


async def test_schedule_respects_daily_limit(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=901)
    await _daily_limit(db_session, max_allowed=0)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    result = await operations.schedule(db_session, product)

    assert result.outcome == "limit_reached"
    assert not result.ok
    await db_session.refresh(product)
    assert product.status == ProductStatus.ELIGIBLE  # unchanged


async def test_schedule_with_no_daily_limit_configured(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=902)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    result = await operations.schedule(db_session, product)

    assert result.outcome == "no_daily_limit_configured"


async def test_schedule_rejects_wrong_status(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=903)
    product = await make_product(db_session, channel, message, status=ProductStatus.PARSED)

    result = await operations.schedule(db_session, product)

    assert result.outcome == "invalid_status"
    assert not result.ok


async def test_schedule_duplicate_safety_via_preexisting_idempotency_key(db_session):
    """If an active scheduled_posts row already exists for this exact
    (product, content_type, date) — e.g. a restart replayed the request
    after product.status got reset — the DB-level idempotency key must
    prevent a second row, even though the product's own status says
    ELIGIBLE again."""
    channel, message = await _setup(db_session, telegram_channel_id=904)
    target_date = today_in_timezone(GAZA)
    await _daily_limit(db_session, max_allowed=5)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    existing_key = build_idempotency_key(
        product_id=product.id, content_type=ContentType.POST, target_date=target_date
    )
    db_session.add(
        ScheduledPost(
            product_id=product.id,
            content_type=ContentType.POST,
            status=PostPublishStatus.SCHEDULED,
            scheduled_for=now_in_timezone(GAZA),
            idempotency_key=existing_key,
        )
    )
    await db_session.commit()

    result = await operations.schedule(
        db_session, product, content_type=ContentType.POST, scheduled_for=now_in_timezone(GAZA)
    )

    assert result.outcome == "already_scheduled"
    posts = (
        (await db_session.execute(select(ScheduledPost).where(ScheduledPost.product_id == product.id)))
        .scalars()
        .all()
    )
    assert len(posts) == 1  # no duplicate row created


# --- publish_now -----------------------------------------------------


async def test_publish_now_schedules_for_right_now(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=905)
    await _daily_limit(db_session, max_allowed=5)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    before = now_in_timezone(GAZA)
    result = await operations.publish_now(db_session, product)
    after = now_in_timezone(GAZA)

    assert result.outcome == "scheduled"
    scheduled_post = (
        await db_session.execute(select(ScheduledPost).where(ScheduledPost.product_id == product.id))
    ).scalars().first()
    assert before <= scheduled_post.scheduled_for <= after + timedelta(seconds=5)


async def test_publish_now_respects_daily_limit_even_for_immediate_requests(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=906)
    await _daily_limit(db_session, max_allowed=0)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    result = await operations.publish_now(db_session, product)

    assert result.outcome == "limit_reached"
    await db_session.refresh(product)
    assert product.status == ProductStatus.ELIGIBLE


async def test_publish_now_does_not_call_any_instagram_api(db_session):
    """Sanity check the contract explicitly: outcome is 'scheduled', never
    'published' — publish_now never sets products.status=PUBLISHED."""
    channel, message = await _setup(db_session, telegram_channel_id=907)
    await _daily_limit(db_session, max_allowed=5)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    result = await operations.publish_now(db_session, product)

    assert result.outcome == "scheduled"
    await db_session.refresh(product)
    assert product.status == ProductStatus.SCHEDULED
    assert product.status != ProductStatus.PUBLISHED


# --- skip ------------------------------------------------------------


async def test_skip_marks_product_skipped(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=908)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)

    result = await operations.skip(db_session, product, reason="out of stock")

    assert result.outcome == "skipped"
    await db_session.refresh(product)
    assert product.status == ProductStatus.SKIPPED
    assert "out of stock" in product.rejection_reason


async def test_skip_cancels_active_scheduled_post_and_frees_capacity(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=909)
    await _daily_limit(db_session, max_allowed=1)
    product = await make_product(db_session, channel, message, status=ProductStatus.ELIGIBLE)
    await operations.schedule(db_session, product)
    await db_session.refresh(product)
    assert product.status == ProductStatus.SCHEDULED

    result = await operations.skip(db_session, product)
    assert result.outcome == "skipped"

    scheduled_post = (
        await db_session.execute(select(ScheduledPost).where(ScheduledPost.product_id == product.id))
    ).scalars().first()
    assert scheduled_post.status == PostPublishStatus.CANCELLED

    # capacity should be freed: another product can now take the slot
    other = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, content_hash="other"
    )
    result2 = await operations.schedule(db_session, other)
    assert result2.outcome == "scheduled"


async def test_skip_rejects_already_published(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=910)
    product = await make_product(db_session, channel, message, status=ProductStatus.PUBLISHED)

    result = await operations.skip(db_session, product)

    assert result.outcome == "invalid_status"
    assert not result.ok


# --- retry_failed ------------------------------------------------------


async def test_retry_failed_scheduled_post_is_rescheduled(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=911)
    product = await make_product(db_session, channel, message, status=ProductStatus.FAILED)
    failed_post = ScheduledPost(
        product_id=product.id,
        content_type=ContentType.POST,
        status=PostPublishStatus.FAILED,
        scheduled_for=now_in_timezone(GAZA),
        idempotency_key="failed-key-1",
        last_error="simulated Instagram API timeout",
    )
    db_session.add(failed_post)
    await db_session.commit()

    result = await operations.retry_failed(db_session, product)

    assert result.outcome == "rescheduled"
    await db_session.refresh(failed_post)
    assert failed_post.status == PostPublishStatus.SCHEDULED
    assert failed_post.last_error is None
    await db_session.refresh(product)
    assert product.status == ProductStatus.SCHEDULED


async def test_retry_failed_product_with_no_failed_post_resets_to_eligible(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=912)
    product = await make_product(db_session, channel, message, status=ProductStatus.FAILED)

    result = await operations.retry_failed(db_session, product)

    assert result.outcome == "reset_to_eligible"
    await db_session.refresh(product)
    assert product.status == ProductStatus.ELIGIBLE


async def test_retry_failed_on_healthy_product_is_a_noop(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=913)
    product = await make_product(db_session, channel, message, status=ProductStatus.QUEUED)

    result = await operations.retry_failed(db_session, product)

    assert result.outcome == "nothing_to_retry"
    assert not result.ok
    await db_session.refresh(product)
    assert product.status == ProductStatus.QUEUED  # unchanged


# --- reprioritize ------------------------------------------------------


async def test_reprioritize_sets_score(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=914)
    product = await make_product(db_session, channel, message, status=ProductStatus.QUEUED, score=10)

    result = await operations.reprioritize(db_session, product, 95)

    assert result.outcome == "reprioritized"
    await db_session.refresh(product)
    assert product.score == 95


async def test_reprioritize_does_not_change_status(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=915)
    product = await make_product(db_session, channel, message, status=ProductStatus.QUEUED, score=10)

    await operations.reprioritize(db_session, product, 5)

    await db_session.refresh(product)
    assert product.status == ProductStatus.QUEUED


@pytest.mark.parametrize("bad_score", [-1, 101, 1000, -50])
async def test_reprioritize_rejects_out_of_range_score(db_session, bad_score):
    channel, message = await _setup(db_session, telegram_channel_id=916)
    product = await make_product(db_session, channel, message, status=ProductStatus.QUEUED)

    with pytest.raises(ValidationError):
        await operations.reprioritize(db_session, product, bad_score)


async def test_reprioritize_accepts_boundary_values(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=917)
    product = await make_product(db_session, channel, message, status=ProductStatus.QUEUED)

    await operations.reprioritize(db_session, product, 0)
    await db_session.refresh(product)
    assert product.score == 0

    await operations.reprioritize(db_session, product, 100)
    await db_session.refresh(product)
    assert product.score == 100
