"""
Tests for `app.ai.caption_runner.generate_pending_captions`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable.
"""

import pytest

from app.ai.caption_runner import generate_pending_captions
from app.models.enums import ProductStatus
from app.models.product import Product
from app.parsing.product_service import process_source_message
from tests.factories import make_channel, make_source_message
from tests.fakes import FakeAIClient

pytestmark = pytest.mark.asyncio


async def _make_parsed_product(db_session, *, telegram_channel_id: int, message_id: int, raw_text: str):
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(
        db_session, channel, telegram_message_id=message_id, raw_text=raw_text
    )
    result = await process_source_message(db_session, message)
    assert result.outcome == "created"
    return await db_session.get(Product, result.product_id)


async def test_processes_only_parsed_products(db_session):
    product_1 = await _make_parsed_product(
        db_session, telegram_channel_id=400, message_id=1, raw_text="Nike Air Max\n30 KWD"
    )
    product_2 = await _make_parsed_product(
        db_session, telegram_channel_id=401, message_id=1, raw_text="Adidas Shoes\n25 KWD"
    )
    # A product not at PARSED (e.g. already VALIDATED from a prior run)
    # must be skipped.
    product_2.status = ProductStatus.VALIDATED
    await db_session.commit()

    client = FakeAIClient(
        responses=['["Nike Air Max available now, message us for details!"]']
    )
    summary = await generate_pending_captions(db_session, client)

    processed_ids = {r.product_id for r in summary.results}
    assert processed_ids == {product_1.id}
    assert product_2.id not in processed_ids
    assert summary.products_processed == 1
    assert summary.selected == 1


async def test_summary_counts_mixed_outcomes(db_session):
    good_product = await _make_parsed_product(
        db_session, telegram_channel_id=402, message_id=1, raw_text="Nike Air Max\n30 KWD"
    )
    bad_product = await _make_parsed_product(
        db_session, telegram_channel_id=403, message_id=1, raw_text="Adidas Shoes\n25 KWD"
    )

    class SequencedClient:
        """Returns a clean caption for the first product, an unsupported-
        claim caption for the second — proves the runner isolates each
        product's outcome independently within one batch."""

        def __init__(self):
            self.call_count = 0

        async def complete(self, *, system_prompt, user_prompt, max_tokens):
            self.call_count += 1
            if "Nike Air Max" in user_prompt:
                return '["Nike Air Max available now, message us for details!"]'
            return '["Adidas Shoes in red, message us for details!"]'

    summary = await generate_pending_captions(db_session, SequencedClient())

    assert summary.products_processed == 2
    assert summary.selected == 1
    assert summary.all_rejected == 1

    outcomes = {r.product_id: r.outcome for r in summary.results}
    assert outcomes[good_product.id] == "selected"
    assert outcomes[bad_product.id] == "all_rejected"


async def test_respects_limit(db_session):
    for i in range(3):
        await _make_parsed_product(
            db_session,
            telegram_channel_id=404,
            message_id=i,
            raw_text=f"Item {i}\n{10 + i} KWD",
        )

    client = FakeAIClient(responses=['["Generic item available now, message us for details!"]'])
    summary = await generate_pending_captions(db_session, client, limit=2)

    assert summary.products_processed == 2
