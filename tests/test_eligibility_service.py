"""
Tests for `app.queue.eligibility_service`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.enums import ErrorSeverity, MediaType, ProductStatus
from app.models.error_log import ErrorLog
from app.queue.eligibility_service import evaluate_pending_products, evaluate_product
from tests.factories import make_caption, make_channel, make_media, make_product, make_source_message, now

pytestmark = pytest.mark.asyncio


async def _setup(db_session, *, telegram_channel_id: int):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(db_session, channel, telegram_message_id=1)
    return channel, message


async def test_fully_qualified_product_becomes_eligible_with_score(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=600)
    product = await make_product(db_session, channel, message, status=ProductStatus.VALIDATED)
    await make_caption(db_session, product)
    await make_media(db_session, message, product=product)

    result = await evaluate_product(db_session, product)

    assert result.outcome == "eligible"
    assert result.score is not None and 0 <= result.score <= 100

    await db_session.refresh(product)
    assert product.status == ProductStatus.ELIGIBLE
    assert product.score == result.score


async def test_missing_caption_stays_validated_and_logs_reason(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=601)
    product = await make_product(db_session, channel, message, status=ProductStatus.VALIDATED)
    await make_media(db_session, message, product=product)  # media only, no caption

    result = await evaluate_product(db_session, product)

    assert result.outcome == "not_eligible"
    assert any("caption" in r for r in result.reasons)

    await db_session.refresh(product)
    assert product.status == ProductStatus.VALIDATED  # unchanged

    errors = (
        (await db_session.execute(select(ErrorLog).where(ErrorLog.source == "eligibility")))
        .scalars()
        .all()
    )
    assert len(errors) == 1
    assert errors[0].severity == ErrorSeverity.INFO
    assert errors[0].product_id == product.id


async def test_media_without_downloaded_file_is_not_valid(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=602)
    product = await make_product(db_session, channel, message, status=ProductStatus.VALIDATED)
    await make_caption(db_session, product)
    await make_media(db_session, message, product=product, file_path=None)  # not downloaded

    result = await evaluate_product(db_session, product)

    assert result.outcome == "not_eligible"
    assert any("media" in r for r in result.reasons)
    await db_session.refresh(product)
    assert product.status == ProductStatus.VALIDATED


async def test_document_media_type_is_not_valid_for_eligibility(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=603)
    product = await make_product(db_session, channel, message, status=ProductStatus.VALIDATED)
    await make_caption(db_session, product)
    await make_media(
        db_session, message, product=product, media_type=MediaType.DOCUMENT, file_path="/x/y.pdf"
    )

    result = await evaluate_product(db_session, product)
    assert result.outcome == "not_eligible"


async def test_duplicate_product_never_becomes_eligible(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=604)
    original = await make_product(
        db_session, channel, message, status=ProductStatus.PUBLISHED, content_hash="orig"
    )
    duplicate = await make_product(
        db_session,
        channel,
        message,
        status=ProductStatus.VALIDATED,
        is_duplicate_of=original.id,
        content_hash="dup",
    )
    await make_caption(db_session, duplicate)
    await make_media(db_session, message, product=duplicate)

    result = await evaluate_product(db_session, duplicate)

    assert result.outcome == "not_eligible"
    assert any("duplicate" in r for r in result.reasons)


async def test_evaluate_pending_products_only_processes_validated_status(db_session):
    channel, message = await _setup(db_session, telegram_channel_id=605)

    eligible_candidate = await make_product(
        db_session, channel, message, status=ProductStatus.VALIDATED, content_hash="h1"
    )
    await make_caption(db_session, eligible_candidate)
    await make_media(db_session, message, product=eligible_candidate)

    already_eligible = await make_product(
        db_session, channel, message, status=ProductStatus.ELIGIBLE, content_hash="h2"
    )
    still_parsed = await make_product(
        db_session, channel, message, status=ProductStatus.PARSED, content_hash="h3"
    )

    results = await evaluate_pending_products(db_session)

    processed_ids = {r.product_id for r in results}
    assert processed_ids == {eligible_candidate.id}
    assert already_eligible.id not in processed_ids
    assert still_parsed.id not in processed_ids


async def test_score_reflects_real_product_data(db_session):
    channel = await make_channel(db_session, telegram_channel_id=606)
    message = await make_source_message(
        db_session, channel, telegram_message_id=1, message_date=now() - timedelta(days=1)
    )
    product = await make_product(
        db_session,
        channel,
        message,
        status=ProductStatus.VALIDATED,
        description="A" * 30,  # substantial
        price=None,  # missing price -> should cost points
    )
    await make_caption(db_session, product)
    await make_media(db_session, message, product=product)

    result = await evaluate_product(db_session, product)

    # caption(40) + media(30) + price(0, missing) + description(10) + freshness(1 day old -> 7)
    assert result.score == 40 + 30 + 0 + 10 + 7
