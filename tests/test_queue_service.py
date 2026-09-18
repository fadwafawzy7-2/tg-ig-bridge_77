"""
Tests for `app.queue.queue_service`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

import pytest

from app.models.enums import ProductStatus
from app.queue.queue_service import enqueue_eligible_products
from tests.factories import make_channel, make_product, make_source_message

pytestmark = pytest.mark.asyncio


async def _setup(db_session, *, telegram_channel_id: int):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1)
    return channel, message


async def test_eligible_product_becomes_queued(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=700)
    product = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, score=50
    )

    results = await enqueue_eligible_products(db_session)

    assert len(results) == 1
    assert results[0].product_id == product.id
    await db_session.refresh(product)
    assert product.status == ProductStatus.QUEUED


async def test_only_eligible_status_products_are_touched(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=701)
    validated = await make_product(
        db_session, channel, message, status=ProductStatus.VALIDATED, content_hash="h1"
    )
    eligible = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, content_hash="h2", score=10
    )
    already_queued = await make_product(
        db_session, channel, message, status=ProductStatus.QUEUED, content_hash="h3"
    )

    results = await enqueue_eligible_products(db_session)

    touched_ids = {r.product_id for r in results}
    assert touched_ids == {eligible.id}

    await db_session.refresh(validated)
    await db_session.refresh(already_queued)
    assert validated.status == ProductStatus.VALIDATED
    assert already_queued.status == ProductStatus.QUEUED


async def test_higher_score_products_are_returned_first(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=702)
    low = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, content_hash="low", score=20
    )
    high = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, content_hash="high", score=90
    )
    mid = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, content_hash="mid", score=50
    )

    results = await enqueue_eligible_products(db_session)

    assert [r.product_id for r in results] == [high.id, mid.id, low.id]


async def test_running_twice_is_idempotent_second_run_is_noop(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=703)
    product = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, score=50
    )

    first = await enqueue_eligible_products(db_session)
    second = await enqueue_eligible_products(db_session)

    assert len(first) == 1
    assert len(second) == 0  # nothing left at ELIGIBLE - restart-safe no-op

    await db_session.refresh(product)
    assert product.status == ProductStatus.QUEUED


async def test_limit_bounds_how_many_are_queued_in_one_call(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=704)
    for i in range(3):
        await make_product(
            db_session,
            channel,
            message,
            status=ProductStatus.ELIGIBLE,
            content_hash=f"h{i}",
            score=10 * i,
        )

    results = await enqueue_eligible_products(db_session, limit=2)
    assert len(results) == 2
