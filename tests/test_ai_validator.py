"""
Tests for `app.ai.validator.validate_caption`.

Pure logic, zero dependency (like `app.ai.prompt_builder` /
`app.parsing.text_parser`) — these run with plain `pytest`, no database,
no network, no `AsyncSession`.
"""

from decimal import Decimal

from app.ai.schemas import SourceFacts
from app.ai.validator import validate_caption


def _facts(raw_text: str, price=None, currency=None) -> SourceFacts:
    return SourceFacts(raw_text=raw_text, price=price, currency=currency)


# --- Happy path -------------------------------------------------------


def test_clean_caption_using_only_source_facts_passes():
    facts = _facts("Nike Air Max\n30 KWD\nمقاس 42 متوفر", price=Decimal("30"), currency="KWD")
    result = validate_caption(
        "حذاء Nike Air Max رياضي، مقاس 42 متوفر الآن. راسلنا لمزيد من التفاصيل.", facts
    )
    assert result.is_valid
    assert result.reasons == []


def test_generic_cta_without_new_claims_passes():
    facts = _facts("Wireless Earbuds\n15 KWD")
    result = validate_caption(
        "Wireless Earbuds now available. Message us for details or check the link in bio.",
        facts,
    )
    assert result.is_valid


# --- Price/currency ban -------------------------------------------------


def test_rejects_when_merchant_price_value_appears():
    facts = _facts("Nike Air Max\n30 KWD", price=Decimal("30"), currency="KWD")
    result = validate_caption("Nike Air Max available for 30 only, message us!", facts)
    assert not result.is_valid
    assert any("price" in r.lower() for r in result.reasons)


def test_rejects_when_currency_token_appears_even_without_matching_number():
    facts = _facts("Nike Air Max\n30 KWD", price=Decimal("30"), currency="KWD")
    result = validate_caption("Nike Air Max - only 25 KWD this week!", facts)
    assert not result.is_valid
    assert any("currency" in r.lower() for r in result.reasons)


def test_rejects_arabic_currency_word():
    facts = _facts("ساعة يد\n10 دينار", price=Decimal("10"), currency=None)
    result = validate_caption("ساعة يد أنيقة بسعر 10 دينار فقط", facts)
    assert not result.is_valid


def test_does_not_falsely_flag_unrelated_digits_as_price():
    # "42" is a size in the source, not the price (30) — must not be
    # confused with the price-leak check (that's the separate "unsupported
    # number" check, which correctly allows it because 42 IS in the source).
    facts = _facts("Nike Air Max\nمقاس 42\n30 KWD", price=Decimal("30"), currency="KWD")
    result = validate_caption("Nike Air Max متوفر مقاس 42، راسلنا للتفاصيل", facts)
    assert result.is_valid


# --- Unsupported numbers (sizes/discounts/quantities/warranty) -------


def test_rejects_invented_discount_percentage():
    facts = _facts("Nike Air Max\n30 KWD")
    result = validate_caption("Nike Air Max - 50% off today only!", facts)
    assert not result.is_valid
    assert any("50" in r for r in result.reasons)


def test_rejects_invented_warranty_duration():
    facts = _facts("Wireless Earbuds\n15 KWD")
    result = validate_caption("Wireless Earbuds with 2 year warranty!", facts)
    assert not result.is_valid


def test_accepts_number_that_is_present_in_source():
    facts = _facts("Wireless Earbuds\nBattery life 20 hours\n15 KWD")
    result = validate_caption("Wireless Earbuds with 20 hours battery life, message us!", facts)
    assert result.is_valid


# --- Unsupported claim categories (color/material/quality/shipping/...) -


def test_rejects_invented_color():
    facts = _facts("Nike Air Max\n30 KWD")
    result = validate_caption("Nike Air Max in red, message us for details!", facts)
    assert not result.is_valid
    assert any("color" in r for r in result.reasons)


def test_accepts_color_present_in_source():
    facts = _facts("Nike Air Max, color: red\n30 KWD")
    result = validate_caption("Nike Air Max in red, available now, message us!", facts)
    assert result.is_valid


def test_rejects_invented_material_claim():
    facts = _facts("Leather Bag\n25 KWD")  # "Leather" is in the title/source already
    # so this one should PASS (material present):
    result_ok = validate_caption("Leather bag now available, message us for details!", facts)
    assert result_ok.is_valid

    facts_no_material = _facts("Bag\n25 KWD")
    result_bad = validate_caption("Genuine leather bag, message us for details!", facts_no_material)
    assert not result_bad.is_valid
    assert any("material" in r for r in result_bad.reasons)


def test_rejects_invented_quality_claim():
    facts = _facts("Wireless Earbuds\n15 KWD")
    result = validate_caption("Premium quality wireless earbuds, message us!", facts)
    assert not result.is_valid
    assert any("quality" in r for r in result.reasons)


def test_rejects_invented_shipping_claim():
    facts = _facts("Wireless Earbuds\n15 KWD")
    result = validate_caption("Wireless Earbuds with free shipping, message us!", facts)
    assert not result.is_valid
    assert any("shipping" in r for r in result.reasons)


def test_rejects_invented_warranty_claim_keyword():
    facts = _facts("Wireless Earbuds\n15 KWD")
    result = validate_caption("Wireless Earbuds, fully guaranteed, message us!", facts)
    assert not result.is_valid
    assert any("warranty" in r for r in result.reasons)


def test_accepts_shipping_claim_present_in_source():
    facts = _facts("Wireless Earbuds\nFree shipping included\n15 KWD")
    result = validate_caption("Wireless Earbuds with free shipping, message us!", facts)
    assert result.is_valid


def test_collects_multiple_independent_rejection_reasons_not_just_first():
    facts = _facts("Nike Air Max\n30 KWD")
    result = validate_caption(
        "Nike Air Max in red, 50% off, premium quality, only 30 KWD!", facts
    )
    assert not result.is_valid
    assert len(result.reasons) >= 3


# --- Length sanity -------------------------------------------------


def test_rejects_empty_caption():
    facts = _facts("Nike Air Max\n30 KWD")
    result = validate_caption("   ", facts)
    assert not result.is_valid
    assert result.reasons == ["caption is empty"]


def test_rejects_caption_below_minimum_length():
    facts = _facts("Nike Air Max\n30 KWD")
    result = validate_caption("Hi!", facts, min_length=10)
    assert not result.is_valid
    assert any("shorter than" in r for r in result.reasons)


def test_rejects_caption_above_maximum_length():
    facts = _facts("Nike Air Max\n30 KWD")
    long_caption = "Nike Air Max available now, message us for details. " * 60
    result = validate_caption(long_caption, facts, max_length=200)
    assert not result.is_valid
    assert any("exceeds" in r for r in result.reasons)


# --- Hardening round 2: generic descriptive claims -------------------


def test_rejects_generic_descriptive_claim_not_in_source_english():
    facts = _facts("Nike Air Max\n30 KWD")
    result = validate_caption(
        "Nike Air Max, very comfortable and practical for daily use, message us!", facts
    )
    assert not result.is_valid
    assert any("comfortable" in r for r in result.reasons)
    assert any("practical" in r for r in result.reasons)


def test_rejects_generic_descriptive_claim_not_in_source_arabic():
    facts = _facts("حذاء رياضي\n30 دينار")
    result = validate_caption("حذاء رياضي مريح جداً ومناسب للاستخدام اليومي، راسلنا", facts)
    assert not result.is_valid
    assert any("مريح" in r for r in result.reasons)


def test_accepts_descriptive_claim_that_is_actually_in_source():
    facts = _facts("Leather Bag, very durable and comfortable\n25 KWD")
    result = validate_caption(
        "Leather bag, durable and comfortable, message us for details!", facts
    )
    assert result.is_valid


def test_word_level_matching_rejects_cross_category_substitution():
    # Source mentions ONE color (blue). Caption claims a DIFFERENT color
    # (red). A category-level check would have wrongly allowed this
    # (both are "color" keywords, and the category is present in the
    # source) — the word-level check must catch it.
    facts = _facts("Nike Air Max, color: blue\n30 KWD")
    result = validate_caption("Nike Air Max in red, message us for details!", facts)
    assert not result.is_valid
    assert any("red" in r for r in result.reasons)


# --- Hardening round 2: price protection independent of facts.price --


def test_rejects_price_value_from_raw_text_even_when_facts_price_is_none():
    # Mirrors Phase 4's real behavior for a bare, nationality-less
    # currency word ("دينار" alone) — text_parser.py deliberately leaves
    # price/currency as None for exactly this case (too ambiguous to
    # commit to a specific ISO code) even though a human reading the text
    # can clearly see "30 دينار" is the price.
    facts = _facts("ساعة يد رجالية 30 دينار", price=None, currency=None)
    result = validate_caption("ساعة يد رجالية أنيقة، السعر 30 فقط لفترة محدودة", facts)
    assert not result.is_valid
    assert any("price-like value" in r for r in result.reasons)


def test_clean_caption_still_passes_when_facts_price_is_none():
    facts = _facts("ساعة يد رجالية 30 دينار", price=None, currency=None)
    result = validate_caption("ساعة يد رجالية متوفرة الآن، راسلنا للتفاصيل", facts)
    assert result.is_valid


def test_rejects_dollar_symbol_price_even_without_extracted_price():
    facts = _facts("Item\n$50 only", price=None, currency=None)
    result = validate_caption("Item available now for 50, message us!", facts)
    assert not result.is_valid


def test_rejects_price_indicator_word_without_currency_symbol():
    facts = _facts("Item\nThe price is 40, message for more info", price=None, currency=None)
    result = validate_caption("Item now available for 40, message us!", facts)
    assert not result.is_valid


def test_rejects_decimal_price_variant_leak():
    facts = _facts("Item\n12.500 BHD", price=Decimal("12.500"), currency="BHD")
    result = validate_caption("Item available now for 12.5, message us!", facts)
    assert not result.is_valid


def test_does_not_treat_a_random_unrelated_number_as_price():
    # "42" is a size with zero price evidence anywhere near it — must NOT
    # be blocked just because it's a number in the text.
    facts = _facts("Nike Air Max\nSize 42 available\n30 KWD", price=Decimal("30"), currency="KWD")
    result = validate_caption("Nike Air Max, size 42 available, message us!", facts)
    assert result.is_valid


def test_does_not_falsely_reject_a_fully_clean_caption_after_hardening():
    facts = _facts(
        "Wireless Earbuds\nBattery life 20 hours\nFree shipping included\n15 KWD",
        price=Decimal("15"),
        currency="KWD",
    )
    result = validate_caption(
        "Wireless Earbuds with 20 hours battery life and free shipping, message us for details!",
        facts,
    )
    assert result.is_valid
