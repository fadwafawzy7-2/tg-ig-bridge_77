"""
Deterministic fact-check of one generated caption against its `SourceFacts`.

Pure Python (regex/substring matching only) — zero I/O, zero third-party
dependency, exactly like `app/parsing/text_parser.py`. This is the actual
enforcement of the Phase 5 hard requirements; the prompt in
`prompt_builder.py` is only a request to the model, not a guarantee, so
every candidate the model returns is re-checked here before it may ever
be marked `is_selected=True`.

What is checked, in order (all reasons collected, not just the first):
1. Basic length sanity (empty / too short / too long for Instagram).
2. The merchant price must never appear — as a currency/price-unit word
   (outright banned, always), as the specific `products.price` value if
   one was extracted, AND as any number that has clear price evidence in
   `raw_text` even when `products.price` is `None` (see
   `_extract_price_evidenced_numbers` — this is what makes the price
   protection hold even when Phase 4 deliberately left `price` unset for
   an ambiguous currency word). A bare number with no such evidence is
   never treated as a price.
3. No number in the caption may be "new" (absent from the source text) —
   the primary defense against invented sizes, discounts, quantities, or
   warranty durations, since those are almost always expressed as
   numbers.
4. No claim-category keyword (size/color/material/quality/shipping/
   warranty/discount — see `claim_lexicon.py`) may appear in the caption
   unless that EXACT keyword also appears in the source text (word-level,
   not category-level — see `claim_lexicon.py`'s docstring for why).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.ai.claim_lexicon import CLAIM_CATEGORIES
from app.ai.schemas import SourceFacts, ValidationResult

DEFAULT_MIN_LENGTH = 10
DEFAULT_MAX_LENGTH = 2200  # Instagram's own caption length limit

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

# Same numeric shape as `app.parsing.text_parser._AMOUNT` (kept as an
# independent constant rather than imported: this module is deliberately
# zero-dependency, including no dependency on `app.parsing`, so it stays
# trivially unit-testable on its own).
_AMOUNT_PATTERN = r"\d+(?:[.,]\d{1,3})?"

# Currency/price-unit words. If ANY of these appear in a caption at all,
# it's rejected outright — regardless of whether a specific price number
# also appears — because a currency word alone still discloses that this
# is priced merchandise, which is exactly what "the merchant price must
# never appear" is guarding against.
#
# Deliberately broader than `text_parser._CURRENCY_PATTERNS`: that module
# excludes bare, nationality-less currency words ("دينار", "ريال", ...)
# because they're too ambiguous to safely COMMIT TO as a specific ISO
# currency code. Here the goal is the opposite kind of conservatism — we
# don't need to know *which* dinar, we just need to know "this is a price
# word" — so the bare/ambiguous forms are included on purpose.
_CURRENCY_TOKENS = [
    "kwd",
    "kd",
    "usd",
    "sar",
    "aed",
    "bhd",
    "omr",
    "qar",
    "egp",
    "jod",
    "$",
    "€",
    "£",
    "دينار",
    "د.ك",
    "ريال",
    "درهم",
    "جنيه",
    "دولار",
]

# Explicit "this number is a price" indicator words/phrases — catches a
# price stated without any currency word right next to it (e.g. "السعر
# 30", "price: 30").
_PRICE_INDICATOR_WORDS = [
    "السعر",
    "سعر",
    "بسعر",
    "ب سعر",
    "priced at",
    "price",
    "cost",
]


def _normalize(text: str) -> str:
    return text.lower()


def _contains(haystack: str, keyword: str) -> bool:
    return keyword in haystack


def _price_variants(price: Decimal) -> set[str]:
    """Textual forms of `price` we consider "the price appearing".

    Covers the plain integer form (`30` for `Decimal("30.00")`) and the
    normalized decimal form (`12.5` for `Decimal("12.50")`), since both
    are plausible ways the number could leak into free text.
    """
    variants = {str(price.normalize())}
    if price == price.to_integral_value():
        variants.add(str(int(price)))
    return variants


def _numeric_string_variants(raw_number: str) -> set[str]:
    """Same normalization as `_price_variants`, but starting from a raw
    numeric substring pulled out of free text (e.g. `"30"`, `"12,500"`)
    rather than an already-parsed `Decimal`. Falls back to just the raw
    string if it can't be parsed as a number (malformed thousands
    separators etc.) — never raises, since this only ever widens what
    counts as "blocked", not what's parsed as `products.price`."""
    variants = {raw_number}
    try:
        value = Decimal(raw_number.replace(",", "."))
    except InvalidOperation:
        return variants
    variants |= _price_variants(value)
    return variants


def _extract_price_evidenced_numbers(raw_text: str) -> set[str]:
    """Numbers in `raw_text` that have clear price evidence next to them:
    a currency word/symbol (`_CURRENCY_TOKENS`) or an explicit price
    indicator word (`_PRICE_INDICATOR_WORDS`) immediately adjacent.

    Deliberately NOT "any number in the text" — a bare number with no
    such evidence is never treated as a price (e.g. a size, quantity, or
    duration elsewhere in the text must not get swept up here). This is
    what closes the gap where `products.price is None` (Phase 4 leaves it
    unset for a genuinely ambiguous currency word) would otherwise mean
    "no price protection at all" for that product.
    """
    found: set[str] = set()

    for token in _CURRENCY_TOKENS:
        escaped = re.escape(token)
        for match in re.finditer(rf"({_AMOUNT_PATTERN})\s*{escaped}", raw_text, re.IGNORECASE):
            found.add(match.group(1))
        for match in re.finditer(rf"{escaped}\s*({_AMOUNT_PATTERN})", raw_text, re.IGNORECASE):
            found.add(match.group(1))

    for word in _PRICE_INDICATOR_WORDS:
        escaped = re.escape(word)
        for match in re.finditer(rf"{escaped}\D{{0,6}}({_AMOUNT_PATTERN})", raw_text, re.IGNORECASE):
            found.add(match.group(1))

    return found


def _find_price_leak(caption: str, facts: SourceFacts) -> str | None:
    normalized_caption = _normalize(caption)

    for token in _CURRENCY_TOKENS:
        if token in normalized_caption:
            return f"caption mentions a currency/price unit ('{token}'), which is never allowed"

    banned_values: set[str] = set()
    if facts.price is not None:
        banned_values |= _price_variants(facts.price)
    for evidenced_number in _extract_price_evidenced_numbers(facts.raw_text):
        banned_values |= _numeric_string_variants(evidenced_number)

    for value in banned_values:
        if re.search(rf"(?<!\d){re.escape(value)}(?!\d)", caption):
            return (
                f"caption contains a price-like value ('{value}') that has clear price "
                "evidence in the source text — the merchant price must never appear"
            )

    return None


def _find_unsupported_numbers(caption: str, facts: SourceFacts) -> list[str]:
    # Extracted as tokens (via the same regex used on the caption), not a
    # substring check against the raw text — a substring check would treat
    # an invented '5' as "supported" merely because the source contains
    # '25' (since '5' is a substring of '25'), which defeats the point of
    # this check.
    source_numbers = {m.group(0) for m in _NUMBER_RE.finditer(facts.raw_text)}
    reasons: list[str] = []
    for match in _NUMBER_RE.finditer(caption):
        number = match.group(0)
        if number not in source_numbers:
            reasons.append(
                f"caption contains number '{number}' that does not appear anywhere in the "
                "source text (possible invented size, discount, quantity, or warranty term)"
            )
    return reasons


def _find_unsupported_claims(caption: str, facts: SourceFacts) -> list[str]:
    """Word-level (not category-level) claim check — see
    `claim_lexicon.py`'s docstring for the reasoning: a keyword in the
    caption must itself appear in the source text, not merely some other
    keyword from the same category."""
    normalized_caption = _normalize(caption)
    normalized_source = _normalize(facts.raw_text)
    reasons: list[str] = []

    for category, keywords in CLAIM_CATEGORIES.items():
        for keyword in keywords:
            if _contains(normalized_caption, keyword) and not _contains(normalized_source, keyword):
                reasons.append(
                    f"caption makes a '{category}' claim ('{keyword}') that does not appear "
                    "word-for-word anywhere in the source text"
                )
    return reasons


def validate_caption(
    caption: str,
    facts: SourceFacts,
    *,
    min_length: int = DEFAULT_MIN_LENGTH,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> ValidationResult:
    """Validate one caption string against `facts`. Never raises."""
    reasons: list[str] = []

    stripped = caption.strip()
    if not stripped:
        return ValidationResult.fail("caption is empty")
    if len(stripped) < min_length:
        reasons.append(f"caption is shorter than the minimum allowed length ({min_length} chars)")
    if len(stripped) > max_length:
        reasons.append(
            f"caption exceeds Instagram's caption length limit ({max_length} chars)"
        )

    price_leak = _find_price_leak(stripped, facts)
    if price_leak is not None:
        reasons.append(price_leak)

    reasons.extend(_find_unsupported_numbers(stripped, facts))
    reasons.extend(_find_unsupported_claims(stripped, facts))

    if reasons:
        return ValidationResult.fail(*reasons)
    return ValidationResult.ok()
