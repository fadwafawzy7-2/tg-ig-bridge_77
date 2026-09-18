"""Plain result type for `text_parser.parse_product_fields`."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ParsedProductFields:
    """Everything extracted from one source message's raw text.

    `title` and `description` are always populated (from real text,
    verbatim) whenever this object exists at all — `parse_product_fields`
    returns `None` instead of this type when there's no usable text.
    `price`/`currency` are populated together or not at all: see
    `text_parser.py` for exactly when they're considered unambiguous
    enough to extract.
    """

    title: str
    description: str
    price: Decimal | None
    currency: str | None
    content_hash: str
