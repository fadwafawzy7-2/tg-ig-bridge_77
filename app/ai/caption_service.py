"""
Turns a `products` row (status=PARSED) into one or more persisted
`captions` rows, validated against its source text, with at most one
selected as publish-ready.

Mirrors `app.parsing.product_service.process_source_message`: one
product's failure (AI call error, all candidates rejected) is isolated —
logged to `errors`, never raised out of a batch run — and NEVER blocks
processing of the rest of a batch. See `caption_runner.py`.

Product status transitions used here (both already defined in Phase 2's
`ProductStatus` enum — no new status was added for this phase):
    PARSED --(a caption passes validation)--> VALIDATED
    PARSED --(every candidate rejected, or the AI call failed)--> PARSED (unchanged)

A product is deliberately left at PARSED (not moved to REJECTED) when
every candidate fails validation or the AI call errors out: REJECTED is
reserved for a product-level decision (e.g. a later duplicate/business
rule), whereas a caption failure is just as likely to be a transient
model hiccup — leaving it at PARSED means a later re-run of
`generate_pending_captions()` will simply try it again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ai_client import AIClient, AIProviderError
from app.ai.caption_generator import generate_captions
from app.ai.schemas import SourceFacts
from app.ai.validator import validate_caption
from app.core.config import get_settings
from app.models.caption import Caption
from app.models.enums import CaptionValidationStatus, ErrorSeverity, ProductStatus
from app.models.error_log import ErrorLog
from app.models.product import Product

logger = logging.getLogger(__name__)

ERROR_SOURCE = "ai_captioning"


@dataclass(frozen=True)
class CaptionProcessResult:
    product_id: int
    outcome: str  # "selected" | "all_rejected" | "no_source_text" | "failed"
    selected_caption_id: int | None = None
    candidate_count: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def _existing_version_count(session: AsyncSession, product_id: int) -> int:
    result = await session.execute(select(Caption).where(Caption.product_id == product_id))
    return len(result.scalars().all())


async def process_product(
    session: AsyncSession,
    product: Product,
    ai_client: AIClient,
    *,
    n_candidates: int | None = None,
) -> CaptionProcessResult:
    settings = get_settings()
    n = n_candidates if n_candidates is not None else settings.AI_CAPTION_CANDIDATES

    # Load the source text directly from source_messages.raw_text — the
    # ONLY data the AI (and the validator) may treat as ground truth.
    await session.refresh(product, attribute_names=["primary_source_message"])
    source_message = product.primary_source_message
    raw_text = source_message.raw_text if source_message is not None else None

    if not raw_text or not raw_text.strip():
        # Defensive only: a product only reaches PARSED when its source
        # message had non-empty raw_text (see product_service.py), so
        # this should not normally happen.
        logger.warning("Product %s has no usable source raw_text; skipping.", product.id)
        return CaptionProcessResult(product_id=product.id, outcome="no_source_text")

    facts = SourceFacts(raw_text=raw_text, price=product.price, currency=product.currency)

    try:
        candidates = await generate_captions(
            ai_client,
            facts,
            n=n,
            max_tokens=settings.AI_CAPTION_MAX_TOKENS,
            generated_by=settings.AI_MODEL,
        )
    except AIProviderError as exc:
        logger.exception("AI generation failed for product_id=%s", product.id)
        await session.rollback()
        session.add(
            ErrorLog(
                source=ERROR_SOURCE,
                severity=ErrorSeverity.ERROR,
                message=f"AI generation failed for product {product.id}: {exc}",
                product_id=product.id,
            )
        )
        await session.commit()
        return CaptionProcessResult(product_id=product.id, outcome="failed", error=str(exc))

    if not candidates:
        logger.warning("AI returned no parseable candidates for product_id=%s", product.id)
        await session.rollback()
        session.add(
            ErrorLog(
                source=ERROR_SOURCE,
                severity=ErrorSeverity.WARNING,
                message=f"AI returned no parseable caption candidates for product {product.id}",
                product_id=product.id,
            )
        )
        await session.commit()
        return CaptionProcessResult(product_id=product.id, outcome="failed", error="empty_response")

    start_version = await _existing_version_count(session, product.id) + 1

    selected_caption: Caption | None = None
    rejected_reasons: list[str] = []

    for offset, candidate in enumerate(candidates):
        result = validate_caption(
            candidate.text,
            facts,
            min_length=settings.AI_MIN_CAPTION_LENGTH,
            max_length=settings.AI_MAX_CAPTION_LENGTH,
        )
        passed = result.is_valid and selected_caption is None
        caption_row = Caption(
            product_id=product.id,
            language=None,
            text=candidate.text,
            version=start_version + offset,
            is_selected=passed,
            generated_by=candidate.generated_by,
            validation_status=(
                CaptionValidationStatus.PASSED if result.is_valid else CaptionValidationStatus.REJECTED
            ),
            rejection_reason="; ".join(result.reasons) if result.reasons else None,
        )
        session.add(caption_row)
        if passed:
            selected_caption = caption_row
        elif not result.is_valid:
            rejected_reasons.append(f"v{caption_row.version}: {caption_row.rejection_reason}")

    if selected_caption is not None:
        product.status = ProductStatus.VALIDATED
        await session.commit()
        await session.refresh(selected_caption)
        return CaptionProcessResult(
            product_id=product.id,
            outcome="selected",
            selected_caption_id=selected_caption.id,
            candidate_count=len(candidates),
        )

    # Every candidate was rejected — persist them (audit trail) but do
    # not advance product status, and log a clear, specific reason.
    session.add(
        ErrorLog(
            source=ERROR_SOURCE,
            severity=ErrorSeverity.WARNING,
            message=(
                f"All {len(candidates)} caption candidate(s) for product {product.id} "
                f"failed validation: {' | '.join(rejected_reasons)}"
            ),
            product_id=product.id,
        )
    )
    await session.commit()
    return CaptionProcessResult(
        product_id=product.id, outcome="all_rejected", candidate_count=len(candidates)
    )
