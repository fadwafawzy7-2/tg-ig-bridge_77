"""
Tests for `app.parsing.product_service.process_source_message`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import ProductStatus, SourceMessageStatus
from app.models.error_log import ErrorLog
from app.models.product import Product
from app.models.product_source_message import ProductSourceMessage
from app.parsing.product_service import process_source_message
from tests.factories import make_channel, make_source_message

pytestmark = pytest.mark.asyncio


async def test_creates_product_with_parsed_fields_and_traceability(db_session):
    channel = await make_channel(db_session, telegram_channel_id=100, title="Kuwait Deals")
    message = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="Nike Air Max\n30 KWD"
    )

    result = await process_source_message(db_session, message)

    assert result.ok
    assert result.outcome == "created"
    product = await db_session.get(Product, result.product_id)
    assert product.status == ProductStatus.PARSED
    assert product.title == "Nike Air Max"
    assert product.description == "Nike Air Max\n30 KWD"
    assert product.price == Decimal("30")
    assert product.currency == "KWD"

    # Source traceability (Phase 2/3 requirement) must be intact.
    assert product.primary_source_message_id == message.id
    assert product.channel_id == channel.id
    assert product.source_channel_id == channel.telegram_channel_id
    assert product.source_channel_name == channel.channel_title
    assert product.source_message_id == message.telegram_message_id
    assert product.source_message_date == message.message_date

    await db_session.refresh(message)
    assert message.status == SourceMessageStatus.PROCESSED


async def test_message_with_no_text_is_ignored_and_creates_no_product(db_session):
    channel = await make_channel(db_session, telegram_channel_id=101)
    message = await make_source_message(db_session, channel, telegram_message_id=1, raw_text=None)

    result = await process_source_message(db_session, message)

    assert result.outcome == "ignored"
    assert result.product_id is None
    await db_session.refresh(message)
    assert message.status == SourceMessageStatus.IGNORED

    products = (await db_session.execute(select(Product))).scalars().all()
    assert products == []


async def test_duplicate_content_is_linked_not_duplicated(db_session):
    channel = await make_channel(db_session, telegram_channel_id=102)
    first_message = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="Nike Air Max\n30 KWD"
    )
    second_message = await make_source_message(
        db_session, channel, telegram_message_id=2, raw_text="nike   air max!!\n30 KWD"
    )

    first_result = await process_source_message(db_session, first_message)
    second_result = await process_source_message(db_session, second_message)

    assert first_result.outcome == "created"
    assert second_result.outcome == "duplicate_linked"
    assert second_result.product_id == first_result.product_id

    # Exactly one product row must exist for this content.
    products = (await db_session.execute(select(Product))).scalars().all()
    assert len(products) == 1

    # Per Phase 2 design, the FIRST (discovery) message's traceability is
    # via `product.primary_source_message_id` — it does NOT also get a
    # product_source_messages row. Only ADDITIONAL occurrences (the
    # second message here) are recorded in that junction table.
    product = products[0]
    assert product.primary_source_message_id == first_message.id

    links = (
        (
            await db_session.execute(
                select(ProductSourceMessage).where(
                    ProductSourceMessage.product_id == first_result.product_id
                )
            )
        )
        .scalars()
        .all()
    )
    linked_message_ids = {link.source_message_id for link in links}
    assert linked_message_ids == {second_message.id}


    await db_session.refresh(second_message)
    assert second_message.status == SourceMessageStatus.PROCESSED


async def test_duplicate_link_never_overwrites_the_original_product_fields(db_session):
    channel = await make_channel(db_session, telegram_channel_id=103)
    first_message = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="Original Title\n30 KWD"
    )
    # Same normalized content_hash (case/punctuation differ only), but a
    # literally different title string, to prove the original wins.
    second_message = await make_source_message(
        db_session, channel, telegram_message_id=2, raw_text="original   title!!!\n30 KWD"
    )

    await process_source_message(db_session, first_message)
    await process_source_message(db_session, second_message)

    products = (await db_session.execute(select(Product))).scalars().all()
    assert len(products) == 1
    assert products[0].title == "Original Title"


async def test_different_content_creates_separate_products(db_session):
    channel = await make_channel(db_session, telegram_channel_id=104)
    message_a = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="Nike Shoes\n30 KWD"
    )
    message_b = await make_source_message(
        db_session, channel, telegram_message_id=2, raw_text="Adidas Shoes\n30 KWD"
    )

    result_a = await process_source_message(db_session, message_a)
    result_b = await process_source_message(db_session, message_b)

    assert result_a.outcome == "created"
    assert result_b.outcome == "created"
    assert result_a.product_id != result_b.product_id


async def test_ambiguous_currency_creates_product_without_price(db_session):
    channel = await make_channel(db_session, telegram_channel_id=105)
    message = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="ساعة يد رجالية 30 دينار"
    )

    result = await process_source_message(db_session, message)

    assert result.outcome == "created"
    product = await db_session.get(Product, result.product_id)
    assert product.price is None
    assert product.currency is None
    assert product.title == "ساعة يد رجالية 30 دينار"


async def test_failure_is_isolated_logged_and_marks_message_failed(db_session):
    channel = await make_channel(db_session, telegram_channel_id=106)
    message = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text="Broken Product\n30 KWD"
    )
    # Force a real DB failure (FK violation) by pointing the message at a
    # channel that doesn't exist, without going through normal flow.
    message.channel_id = 999_999

    result = await process_source_message(db_session, message)

    assert not result.ok
    assert result.outcome == "failed"

    await db_session.refresh(message)
    assert message.status == SourceMessageStatus.FAILED

    errors = (
        (await db_session.execute(select(ErrorLog).where(ErrorLog.source == "product_parser")))
        .scalars()
        .all()
    )
    assert len(errors) == 1
    assert errors[0].source_message_id == message.id

    products = (await db_session.execute(select(Product))).scalars().all()
    assert products == []
