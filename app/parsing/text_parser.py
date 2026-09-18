"""
Deterministic, rule-based extraction of product fields from Telegram
message text. NO AI/ML/LLM involved — plain regex and string handling
only, exactly as required.

Source of truth: `source_messages.raw_text` is the ONLY input. Nothing
here ever looks at media/images. Nothing is invented: if the text doesn't
contain a clear, unambiguous signal for a field, that field is left
`None` rather than guessed.

This module has zero I/O and zero dependency on `app.models` or any
database/network library on purpose — it's pure text-in, data-out logic,
so it's exhaustively unit-testable (and was actually executed directly in
the environment this was written in, without needing SQLAlchemy).
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation

from app.parsing.schemas import ParsedProductFields

# Column limit on `products.title` (see app/models/product.py) — title is
# truncated to fit, never silently dropped.
TITLE_MAX_LENGTH = 500

_AMOUNT = r"(?<![\d.,])\d+(?:[.,]\d{1,2})?(?![\d.,])"

# Ordered most-specific-first: a multi-word qualified phrase like
# "دينار كويتي" must be tried before any shorter/bare pattern that could
# also appear inside it. Deliberately EXCLUDES bare, nationality-less
# currency words ("دينار", "ريال", "درهم", "جنيه" on their own) — those are
# genuinely ambiguous (Kuwaiti vs. Jordanian vs. Bahraini dinar, Saudi vs.
# Qatari riyal, etc.) and guessing one would be inventing information the
# merchant didn't actually state.
_CURRENCY_PATTERNS: list[tuple[str, str]] = [
    (r"دينار\s*كويتي", "KWD"),
    (r"دينار\s*أردني", "JOD"),
    (r"دينار\s*اردني", "JOD"),
    (r"دينار\s*بحريني", "BHD"),
    (r"ريال\s*سعودي", "SAR"),
    (r"ريال\s*قطري", "QAR"),
    (r"جنيه\s*مصري", "EGP"),
    (r"درهم\s*إماراتي", "AED"),
    (r"درهم\s*اماراتي", "AED"),
    (r"د\.?\s?ك\b", "KWD"),
    (r"\bKWD\b", "KWD"),
    (r"\bKD\b", "KWD"),
    (r"\bUSD\b", "USD"),
    (r"\$", "USD"),
    (r"\bSAR\b", "SAR"),
    (r"\bAED\b", "AED"),
    (r"\bEGP\b", "EGP"),
    (r"\bQAR\b", "QAR"),
    (r"\bBHD\b", "BHD"),
    (r"\bOMR\b", "OMR"),
    (r"\bJOD\b", "JOD"),
]


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        # Malformed number text (e.g. "1,234.56" thousands-separator
        # style) — rather than guess which separator was meant, treat as
        # "no reliable price found".
        return None


def extract_price_currency(text: str) -> tuple[Decimal, str] | None:
    """Find the first unambiguous (amount, ISO-ish currency code) pair.

    Tries "<amount> <currency>" and "<currency> <amount>" for each known
    currency pattern, in most-specific-first order. Returns `None` if no
    unambiguous currency indicator is found — a bare number with no
    currency word/symbol is never treated as a price.
    """
    for pattern, code in _CURRENCY_PATTERNS:
        amount_then_currency = re.search(rf"({_AMOUNT})\s*{pattern}", text, re.IGNORECASE)
        if amount_then_currency:
            amount = _to_decimal(amount_then_currency.group(1))
            if amount is not None:
                return amount, code

        currency_then_amount = re.search(rf"{pattern}\s*({_AMOUNT})", text, re.IGNORECASE)
        if currency_then_amount:
            amount = _to_decimal(currency_then_amount.group(1))
            if amount is not None:
                return amount, code

    return None


def extract_title(raw_text: str) -> str:
    """First non-blank line of the text, truncated to the DB column limit."""
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:TITLE_MAX_LENGTH]
    # raw_text was non-blank overall (caller already checked) but somehow
    # every line is blank after stripping — fall back to the whole text.
    return raw_text.strip()[:TITLE_MAX_LENGTH]


def compute_content_hash(title: str, price: Decimal | None, currency: str | None) -> str:
    """A deterministic fingerprint for duplicate detection.

    Normalizes the title (lowercase, punctuation stripped, whitespace
    collapsed) so trivial formatting differences (extra spaces, emoji,
    punctuation) between two postings of the same item still hash
    identically. Price/currency are folded in when present so two
    different items that happen to share a title aren't conflated.
    """
    normalized = re.sub(r"[^\w\s]", "", title, flags=re.UNICODE).lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()

    parts = [normalized]
    if price is not None:
        parts.append(f"price:{price.normalize()}")
    if currency is not None:
        parts.append(f"currency:{currency}")

    fingerprint = "|".join(parts)
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def parse_product_fields(raw_text: str | None) -> ParsedProductFields | None:
    """Parse one message's text into product fields.

    Returns `None` when there's no usable text at all (media-only posts,
    blank captions) — there is nothing to extract and nothing should be
    invented in its place.
    """
    if raw_text is None or not raw_text.strip():
        return None

    title = extract_title(raw_text)
    price_currency = extract_price_currency(raw_text)
    price, currency = price_currency if price_currency is not None else (None, None)
    content_hash = compute_content_hash(title, price, currency)

    return ParsedProductFields(
        title=title,
        description=raw_text,  # full original text, verbatim — no loss
        price=price,
        currency=currency,
        content_hash=content_hash,
    )
