"""
Tests for `app.ai.caption_service.process_product`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable. Products are created via the real
`product_service.process_source_message` (Phase 4) so these tests exercise
the actual Phase 4 -> Phase 5 handoff, not a hand-built stand-in.
"""

import pytest
from sqlalchemy import select

from app.ai.caption_service import process_product
from app.models.caption import Caption
from app.models.enums import CaptionValidationStatus, ProductStatus
from app.models.error_log import ErrorLog
from app.models.product import Product
from app.parsing.product_service import process_source_message
from tests.factories import make_channel, make_source_message
from tests.fakes import FailingFakeAIClient, FakeAIClient

pytestmark = pytest.mark.asyncio


async def _make_parsed_product(db_session, *, telegram_channel_id: int, raw_text: str) -> Product:
    channel = await make_channel(db_session, telegram_channel_id=telegram_channel_id)
    message = await make_source_message(
        db_session, channel, telegram_message_id=1, raw_text=raw_text
    )
    result = await process_source_message(db_session, message)
    assert result.outcome == "created"
    product = await db_session.get(Product, result.product_id)
    assert product.status == ProductStatus.PARSED
    return product


async def test_valid_candidate_is_selected_and_product_moves_to_validated(db_session):
    product = await _make_parsed_product(
        db_session, telegram_channel_id=300, raw_text="Nike Air Max\n30 KWD"
    )
    client = FakeAIClient(
        responses=['["Nike Air Max available now, message us for details!"]']
    )

    result = await process_product(db_session, product, client, n_candidates=1)

    assert result.outcome == "selected"
    assert result.candidate_count == 1
    assert result.selected_caption_id is not None

    await db_session.refresh(product)
    assert product.status == ProductStatus.VALIDATED

    caption = await db_session.get(Caption, result.selected_caption_id)
    assert caption.is_selected is True
    assert caption.validation_status == CaptionValidationStatus.PASSED
    assert caption.rejection_reason is None
    assert caption.version == 1


async def test_first_valid_candidate_among_several_is_selected(db_session):
    product = await _make_parsed_product(
        db_session, telegram_channel_id=301, raw_text="Nike Air Max\n30 KWD"
    )
    client = FakeAIClient(
        responses=[
            '['
            '"Nike Air Max in red, message us!",'  # invented color -> rejected
            '"Nike Air Max available now, message us for details!",'  # clean -> selected
            '"Nike Air Max, 50% off today!"'  # invented number -> rejected
            "]"
        ]
    )

    result = await process_product(db_session, product, client, n_candidates=3)

    assert result.outcome == "selected"
    assert result.candidate_count == 3

    captions = (
        (await db_session.execute(select(Caption).where(Caption.product_id == product.id)))
        .scalars()
        .all()
    )
    assert len(captions) == 3
    selected = [c for c in captions if c.is_selected]
    rejected = [c for c in captions if not c.is_selected]
    assert len(selected) == 1
    assert len(rejected) == 2
    assert selected[0].text == "Nike Air Max available now, message us for details!"
    for c in rejected:
        assert c.validation_status == CaptionValidationStatus.REJECTED
        assert c.rejection_reason is not None


async def test_all_candidates_rejected_leaves_product_parsed_and_logs_error(db_session):
    product = await _make_parsed_product(
        db_session, telegram_channel_id=302, raw_text="Nike Air Max\n30 KWD"
    )
    client = FakeAIClient(
        responses=['["Nike Air Max in red, message us for details, 50% off today!"]']
    )

    result = await process_product(db_session, product, client, n_candidates=1)

    assert result.outcome == "all_rejected"
    assert result.selected_caption_id is None

    await db_session.refresh(product)
    assert product.status == ProductStatus.PARSED  # unchanged, not advanced

    captions = (
        (await db_session.execute(select(Caption).where(Caption.product_id == product.id)))
        .scalars()
        .all()
    )
    assert len(captions) == 1
    assert captions[0].validation_status == CaptionValidationStatus.REJECTED
    assert captions[0].is_selected is False

    errors = (
        (await db_session.execute(select(ErrorLog).where(ErrorLog.source == "ai_captioning")))
        .scalars()
        .all()
    )
    assert len(errors) == 1
    assert errors[0].product_id == product.id


async def test_merchant_price_never_appears_in_a_selected_caption(db_session):
    product = await _make_parsed_product(
        db_session, telegram_channel_id=303, raw_text="Nike Air Max\n30 KWD"
    )
    client = FakeAIClient(
        responses=['["Nike Air Max only 30 KWD, grab yours today!"]']
    )

    result = await process_product(db_session, product, client, n_candidates=1)

    assert result.outcome == "all_rejected"
    await db_session.refresh(product)
    assert product.status == ProductStatus.PARSED


async def test_ai_provider_failure_is_isolated_and_logged(db_session):
    product = await _make_parsed_product(
        db_session, telegram_channel_id=304, raw_text="Nike Air Max\n30 KWD"
    )
    client = FailingFakeAIClient()

    result = await process_product(db_session, product, client, n_candidates=1)

    assert result.outcome == "failed"
    assert not result.ok

    await db_session.refresh(product)
    assert product.status == ProductStatus.PARSED

    captions = (
        (await db_session.execute(select(Caption).where(Caption.product_id == product.id)))
        .scalars()
        .all()
    )
    assert captions == []

    errors = (
        (await db_session.execute(select(ErrorLog).where(ErrorLog.source == "ai_captioning")))
        .scalars()
        .all()
    )
    assert len(errors) == 1
    assert errors[0].product_id == product.id


async def test_second_run_after_all_rejected_adds_new_versions_not_replaces(db_session):
    product = await _make_parsed_product(
        db_session, telegram_channel_id=305, raw_text="Nike Air Max\n30 KWD"
    )
    bad_client = FakeAIClient(responses=['["Nike Air Max in red, message us!"]'])
    await process_product(db_session, product, bad_client, n_candidates=1)

    good_client = FakeAIClient(
        responses=['["Nike Air Max available now, message us for details!"]']
    )
    result = await process_product(db_session, product, good_client, n_candidates=1)

    assert result.outcome == "selected"
    captions = (
        (await db_session.execute(select(Caption).where(Caption.product_id == product.id)))
        .scalars()
        .all()
    )
    assert len(captions) == 2
    versions = sorted(c.version for c in captions)
    assert versions == [1, 2]


# --- Hardening round 2: price protection independent of products.price -


async def test_price_leaks_are_rejected_even_when_products_price_is_none(db_session):
    # Bare, nationality-less "دينار" — text_parser.py (Phase 4) deliberately
    # leaves products.price/currency as None for this exact case (too
    # ambiguous to commit to a specific ISO currency code). The Phase 5
    # protection must still catch the price leaking into a caption.
    product = await _make_parsed_product(
        db_session, telegram_channel_id=306, raw_text="ساعة يد رجالية\n30 دينار"
    )
    assert product.price is None  # sanity check: confirms this is the ambiguous-currency case

    client = FakeAIClient(responses=['["ساعة يد رجالية، السعر 30 فقط، راسلنا للتفاصيل"]'])
    result = await process_product(db_session, product, client, n_candidates=1)

    assert result.outcome == "all_rejected"
    await db_session.refresh(product)
    assert product.status == ProductStatus.PARSED

    caption = (
        (await db_session.execute(select(Caption).where(Caption.product_id == product.id)))
        .scalars()
        .one()
    )
    assert caption.validation_status == CaptionValidationStatus.REJECTED
    assert "price" in caption.rejection_reason.lower()


async def test_clean_caption_still_selected_when_products_price_is_none(db_session):
    product = await _make_parsed_product(
        db_session, telegram_channel_id=307, raw_text="ساعة يد رجالية\n30 دينار"
    )
    assert product.price is None

    client = FakeAIClient(responses=['["ساعة يد رجالية متوفرة الآن، راسلنا للتفاصيل"]'])
    result = await process_product(db_session, product, client, n_candidates=1)

    assert result.outcome == "selected"
    await db_session.refresh(product)
    assert product.status == ProductStatus.VALIDATED


# --- Hardening round 2: the DB-enforced "never selected unless PASSED" -


async def test_no_selected_caption_ever_has_a_non_passed_validation_status(db_session):
    """Runs a batch of mixed-outcome products and checks the invariant
    holds across every caption row created: `is_selected=True` implies
    `validation_status=PASSED`. (Already guaranteed by a DB CHECK
    constraint — this test exercises it through the real service, not
    just by asserting the constraint exists.)"""
    products = []
    for i, raw_text in enumerate(
        [
            "Nike Air Max\n30 KWD",  # will get a clean caption -> selected
            "Adidas Shoes\n25 KWD",  # will get an invented-color caption -> all rejected
            "ساعة يد رجالية\n30 دينار",  # ambiguous currency; price-leak caption -> all rejected
        ]
    ):
        products.append(
            await _make_parsed_product(db_session, telegram_channel_id=308 + i, raw_text=raw_text)
        )

    responses = {
        "Nike Air Max": '["Nike Air Max available now, message us for details!"]',
        "Adidas Shoes": '["Adidas Shoes in red, message us for details!"]',
        "ساعة يد رجالية": '["ساعة يد رجالية، السعر 30 فقط"]',
    }

    for product in products:
        matching_response = next(v for k, v in responses.items() if k in product.title)
        client = FakeAIClient(responses=[matching_response])
        await process_product(db_session, product, client, n_candidates=1)

    all_captions = (await db_session.execute(select(Caption))).scalars().all()
    assert len(all_captions) == 3  # one candidate per product in this test
    for caption in all_captions:
        if caption.is_selected:
            assert caption.validation_status == CaptionValidationStatus.PASSED
        else:
            # every non-selected caption in this scenario was rejected
            # (none of these products had more than one candidate, so
            # there's no "valid-but-not-first" case here)
            assert caption.validation_status == CaptionValidationStatus.REJECTED

    selected = [c for c in all_captions if c.is_selected]
    assert len(selected) == 1
    assert selected[0].product_id == products[0].id
