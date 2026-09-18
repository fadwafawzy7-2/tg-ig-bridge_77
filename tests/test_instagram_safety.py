"""
Tests for `app.instagram.safety.check_publish_safety`.

Pure logic, zero dependency — run with plain `python`/`pytest`, no
database, no sqlalchemy import.
"""

from decimal import Decimal

from app.instagram.safety import check_publish_safety

BASE_KWARGS = dict(
    product_status="SCHEDULED",
    is_duplicate=False,
    caption_text="Nike Air Max available now, message us for details!",
    caption_is_selected=True,
    caption_validation_status="PASSED",
    raw_text="Nike Air Max\n30 KWD",
    price=Decimal("30"),
    currency="KWD",
)


def _with(**overrides):
    kwargs = dict(BASE_KWARGS)
    kwargs.update(overrides)
    return kwargs


def test_fully_safe_caption_passes():
    result = check_publish_safety(**BASE_KWARGS)
    assert result.is_safe
    assert result.reasons == ()


def test_missing_caption_is_unsafe():
    result = check_publish_safety(
        **_with(caption_text=None, caption_is_selected=False, caption_validation_status=None)
    )
    assert not result.is_safe
    assert any("no caption" in r for r in result.reasons)


def test_unselected_caption_is_unsafe():
    result = check_publish_safety(**_with(caption_is_selected=False))
    assert not result.is_safe
    assert any("not marked as selected" in r for r in result.reasons)


def test_non_passed_validation_status_is_unsafe():
    result = check_publish_safety(**_with(caption_validation_status="REJECTED"))
    assert not result.is_safe
    assert any("not PASSED" in r for r in result.reasons)


def test_pending_validation_status_is_unsafe():
    result = check_publish_safety(**_with(caption_validation_status="PENDING"))
    assert not result.is_safe


# --- Product status gates -------------------------------------------


def test_duplicate_product_status_is_unsafe():
    result = check_publish_safety(**_with(product_status="DUPLICATE"))
    assert not result.is_safe
    assert any("DUPLICATE" in r for r in result.reasons)


def test_rejected_product_status_is_unsafe():
    result = check_publish_safety(**_with(product_status="REJECTED"))
    assert not result.is_safe


def test_skipped_product_status_is_unsafe():
    result = check_publish_safety(**_with(product_status="SKIPPED"))
    assert not result.is_safe


def test_is_duplicate_flag_is_unsafe_even_if_status_looks_fine():
    result = check_publish_safety(**_with(product_status="SCHEDULED", is_duplicate=True))
    assert not result.is_safe
    assert any("duplicate" in r for r in result.reasons)


def test_scheduled_and_published_product_statuses_are_fine():
    for status in ("SCHEDULED", "QUEUED", "ELIGIBLE", "VALIDATED"):
        result = check_publish_safety(**_with(product_status=status))
        assert result.is_safe, f"status={status} should be safe but got {result.reasons}"


# --- Merchant price protection (defense in depth) ------------------------


def test_merchant_price_leak_is_caught_even_with_stale_passed_flag():
    # The caption text itself now mentions the price, even though the
    # caption row's validation_status claims PASSED (simulating stale/
    # tampered data since Phase 5 ran) - the live re-check must catch it.
    result = check_publish_safety(
        **_with(caption_text="Nike Air Max only 30 KWD, buy now!")
    )
    assert not result.is_safe
    assert any("re-validation failed" in r for r in result.reasons)


def test_currency_word_alone_is_caught():
    result = check_publish_safety(
        **_with(caption_text="Nike Air Max available now for KWD, message us!")
    )
    assert not result.is_safe


def test_invented_claim_not_in_source_is_caught():
    result = check_publish_safety(
        **_with(caption_text="Nike Air Max, 50% off today only, message us for details!")
    )
    assert not result.is_safe


def test_missing_raw_text_is_unsafe():
    result = check_publish_safety(**_with(raw_text=None))
    assert not result.is_safe
    assert any("no source text" in r for r in result.reasons)


def test_price_none_in_source_still_bans_currency_words():
    result = check_publish_safety(
        **_with(
            raw_text="Nike Air Max, great shoes",
            price=None,
            currency=None,
            caption_text="Nike Air Max available, message us for details!",
        )
    )
    assert result.is_safe  # clean caption, no currency word, no invented claim


def test_collects_multiple_independent_reasons():
    result = check_publish_safety(
        **_with(
            product_status="REJECTED",
            is_duplicate=True,
            caption_is_selected=False,
        )
    )
    assert not result.is_safe
    assert len(result.reasons) >= 3
