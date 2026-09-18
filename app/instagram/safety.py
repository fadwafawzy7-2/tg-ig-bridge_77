"""
Phase 8 pre-publish safety gate — final belt-and-suspenders checks
immediately before calling Instagram, ON TOP OF (never instead of, never
looser than) Phase 5's caption validation gate and Phase 6's eligibility
gate.

Pure Python: takes plain values (not ORM objects), the same pattern as
`app.queue.eligibility` — zero dependency on `app.models`, so this stays
directly and exhaustively unit-testable with plain `python`/`pytest`, no
sqlalchemy import at all. It DOES import `app.ai.schemas` /
`app.ai.validator` (Phase 5), which are themselves zero-third-party-
dependency modules, so that import doesn't compromise standalone
testability.

`check_publish_safety` re-runs the EXACT SAME `app.ai.validator.validate_caption`
function Phase 5 uses, against a fresh `SourceFacts` built from the
values passed in. If this ever disagrees with Phase 5's original PASSED
verdict, something has changed since Phase 5 ran (e.g. the underlying
product/source data was edited) — publishing is refused rather than
trusting a possibly-stale PASSED flag. This is the concrete mechanism
behind "لا يظهر Merchant Price في Instagram" and "لا يخترع أي معلومات"
holding at PUBLISH time, not just at caption-generation time.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.ai.schemas import SourceFacts
from app.ai.validator import validate_caption

# Product statuses that must never be published, passed as plain strings
# (not an app.models.enums.ProductStatus import) - see module docstring.
_NEVER_PUBLISH_STATUSES = {"DUPLICATE", "REJECTED", "SKIPPED"}


@dataclass(frozen=True)
class SafetyCheckResult:
    is_safe: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def ok(cls) -> "SafetyCheckResult":
        return cls(is_safe=True, reasons=())

    @classmethod
    def fail(cls, *reasons: str) -> "SafetyCheckResult":
        return cls(is_safe=False, reasons=reasons)


def check_publish_safety(
    *,
    product_status: str,
    is_duplicate: bool,
    caption_text: str | None,
    caption_is_selected: bool,
    caption_validation_status: str | None,
    raw_text: str | None,
    price: Decimal | None,
    currency: str | None,
    product_code: str | None = None,
) -> SafetyCheckResult:
    """Never raises. Collects every failing reason (not just the first),
    matching every other safety-check convention in this codebase
    (`app.ai.validator`, `app.queue.eligibility`)."""
    reasons: list[str] = []

    if caption_text is None:
        reasons.append("no caption linked to this scheduled post")
    else:
        if not caption_is_selected:
            reasons.append("linked caption is not marked as selected")
        if caption_validation_status != "PASSED":
            reasons.append(
                f"linked caption validation_status is {caption_validation_status}, not PASSED"
            )

    if product_status in _NEVER_PUBLISH_STATUSES:
        reasons.append(f"product status is {product_status}")
    if is_duplicate:
        reasons.append("product is marked as a duplicate of another product")

    # Final price/unsupported-claim re-check - only meaningful if the
    # above passed (a missing/unselected caption has nothing to re-check).
    if caption_text is not None and not reasons:
        if not raw_text:
            reasons.append("no source text available to re-validate the caption against")
        else:
            facts = SourceFacts(raw_text=raw_text, price=price, currency=currency)
            result = validate_caption(caption_text, facts)
            if not result.is_valid:
                reasons.append(
                    "final pre-publish caption re-validation failed: " + "; ".join(result.reasons)
                )
            # The product code is system metadata, not merchant/product data.
            # Validate the exact caption that will be sent to Instagram.
            if product_code:
                from app.instagram.publisher_service import build_instagram_caption
                final_caption = build_instagram_caption(caption_text, product_code)
                final_result = validate_caption(final_caption, facts)
                if not final_result.is_valid:
                    reasons.append(
                        "final Instagram caption (including product code) failed validation: "
                        + "; ".join(final_result.reasons)
                    )

    if reasons:
        return SafetyCheckResult.fail(*reasons)
    return SafetyCheckResult.ok()
