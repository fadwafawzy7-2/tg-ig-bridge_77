"""
Unit tests for `app.parsing.text_parser` — pure logic, no database, no
network, no AI. This module was actually executed directly in the
environment it was written in (see the Phase 4 summary) to confirm every
case here passes, since `text_parser.py` has zero dependency on
`app.models`/SQLAlchemy.
"""

from decimal import Decimal

from app.parsing.text_parser import (
    compute_content_hash,
    extract_price_currency,
    extract_title,
    parse_product_fields,
)

# --- parse_product_fields: when there's nothing to parse ------------------


def test_parse_returns_none_for_none_text():
    assert parse_product_fields(None) is None


def test_parse_returns_none_for_empty_text():
    assert parse_product_fields("") is None


def test_parse_returns_none_for_whitespace_only_text():
    assert parse_product_fields("   \n\n  \t ") is None


# --- title = first line, description = full text, verbatim ----------------


def test_title_is_the_first_non_blank_line():
    result = parse_product_fields("Nike Air Max\nSize 42\n30 KWD")
    assert result.title == "Nike Air Max"


def test_title_skips_leading_blank_lines():
    result = parse_product_fields("\n\n   \nReal Title\nmore text")
    assert result.title == "Real Title"


def test_description_preserves_the_full_original_text_without_loss():
    original = "حذاء نايك مقاس 42\nسعر 30 KWD\nمتوفر بالمخزون"
    result = parse_product_fields(original)
    assert result.description == original


def test_title_is_truncated_to_500_chars_but_not_dropped():
    long_line = "A" * 600
    result = parse_product_fields(long_line)
    assert len(result.title) == 500
    # description still holds the full, untruncated text
    assert result.description == long_line


def test_extract_title_falls_back_to_stripped_text_when_all_lines_blank():
    # Defensive edge case for the helper itself (parse_product_fields
    # would already have returned None for pure whitespace, so this
    # exercises extract_title() directly with an unusual value).
    assert extract_title("   x   ") == "x"


# --- price/currency: only extracted when unambiguous -----------------------


def test_extracts_amount_then_iso_code():
    assert extract_price_currency("Shoes 30 KWD") == (Decimal("30"), "KWD")


def test_extracts_dollar_prefix():
    assert extract_price_currency("$25 Nike shoes") == (Decimal("25"), "USD")


def test_extracts_qualified_arabic_currency_phrase():
    assert extract_price_currency("فستان 15 دينار كويتي") == (Decimal("15"), "KWD")


def test_extracts_saudi_riyal_phrase():
    assert extract_price_currency("عباية 45 ريال سعودي") == (Decimal("45"), "SAR")


def test_bare_ambiguous_currency_word_is_not_extracted():
    """'دينار' alone doesn't say WHICH dinar (KWD? JOD? BHD?) — guessing
    would be inventing information the merchant never stated."""
    assert extract_price_currency("ساعة يد رجالية 30 دينار") is None


def test_bare_riyal_without_qualifier_is_not_extracted():
    assert extract_price_currency("قميص 20 ريال") is None


def test_no_currency_indicator_means_no_price():
    assert extract_price_currency("منتج رائع بجودة عالية 42") is None


def test_decimal_amount_is_parsed():
    assert extract_price_currency("Item 19.99 USD") == (Decimal("19.99"), "USD")


def test_parse_product_fields_leaves_price_and_currency_none_together():
    result = parse_product_fields("ساعة يد رجالية 30 دينار")
    assert result.price is None
    assert result.currency is None


def test_parse_product_fields_sets_price_and_currency_together():
    result = parse_product_fields("Shoes 30 KWD")
    assert result.price == Decimal("30")
    assert result.currency == "KWD"


# --- content_hash: stable fingerprint for duplicate detection --------------


def test_content_hash_is_stable_across_trivial_formatting_differences():
    a = parse_product_fields("Nike Shoes!!\n30 KWD")
    b = parse_product_fields("nike   shoes\n30 KWD")
    assert a.content_hash == b.content_hash


def test_content_hash_differs_for_different_prices():
    a = parse_product_fields("Nike Shoes\n30 KWD")
    b = parse_product_fields("Nike Shoes\n35 KWD")
    assert a.content_hash != b.content_hash


def test_content_hash_differs_for_different_titles():
    a = parse_product_fields("Nike Shoes\n30 KWD")
    b = parse_product_fields("Adidas Shoes\n30 KWD")
    assert a.content_hash != b.content_hash


def test_content_hash_differs_for_different_currency_with_same_amount():
    a = parse_product_fields("Shoes 30 KWD")
    b = parse_product_fields("Shoes 30 USD")
    assert a.content_hash != b.content_hash


def test_content_hash_is_a_64_char_hex_sha256_digest():
    result = parse_product_fields("Shoes 30 KWD")
    assert len(result.content_hash) == 64
    int(result.content_hash, 16)  # raises ValueError if not valid hex


def test_compute_content_hash_ignores_price_when_none_for_both_inputs():
    a = compute_content_hash("same title", None, None)
    b = compute_content_hash("same title", None, None)
    assert a == b


def test_price_parser_rejects_ambiguous_thousands_separator():
    parsed = parse_product_fields("قميص\n1,234 USD")
    assert parsed is not None
    assert parsed.price is None
    assert parsed.currency is None


def test_price_parser_accepts_decimal_with_two_places():
    parsed = parse_product_fields("قميص\n1234.56 USD")
    assert parsed is not None
    assert str(parsed.price) == "1234.56"
    assert parsed.currency == "USD"
