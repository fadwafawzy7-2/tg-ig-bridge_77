"""
Turns a VALIDATED product into ELIGIBLE (or leaves it VALIDATED, with a
logged reason, if it doesn't yet qualify) — the DB-facing layer around
the pure `eligibility.py` gate. Also computes and stores `products.score`
(via `scoring.py`) for every product evaluated, regardless of outcome,
so score is visible for triage even before a product is eligible.

Mirrors `app.ai.caption_service.process_product`: one product's failure
is isolated (logged to `errors`, never raised out of a batch run) and
never blocks the rest of a batch — see `runner.py`.

A product that fails the eligibility gate is deliberately left at
VALIDATED (not moved to REJECTED/SKIPPED): missing media in particular is
very likely just "not downloaded yet by a future phase", not a permanent
disqualification, so the product simply gets re-evaluated the next time
this runs — exactly like Phase 5 leaves a product at PARSED when every
caption candidate is rejected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.caption import Caption
from app.models.enums import CaptionValidationStatus, ErrorSeverity, MediaType, ProductStatus
from app.models.error_log import ErrorLog
from app.models.media import Media
from app.models.product import Product
from app.queue.eligibility import evaluate_eligibility, is_valid_media_item
from app.queue.scoring import compute_score
from app.queue.time_utils import utcnow

logger = logging.getLogger(__name__)

ERROR_SOURCE = "eligibility"


@dataclass(frozen=True)
class EligibilityProcessResult:
    product_id: int
    outcome: str  # "eligible" | "not_eligible" | "failed"
    score: int | None = None
    reasons: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def _has_selected_passed_caption(session: AsyncSession, product_id: int) -> bool:
    result = await session.execute(
        select(Caption.id).where(
            Caption.product_id == product_id,
            Caption.is_selected.is_(True),
            Caption.validation_status == CaptionValidationStatus.PASSED,
        )
    )
    return result.scalars().first() is not None


async def _has_valid_media(session: AsyncSession, product_id: int) -> bool:
    result = await session.execute(select(Media).where(Media.product_id == product_id))
    media_items = result.scalars().all()
    return any(
        is_valid_media_item(media_type=item.media_type, file_path=item.file_path)
        for item in media_items
    )


async def evaluate_product(session: AsyncSession, product: Product) -> EligibilityProcessResult:
    try:
        has_caption = await _has_selected_passed_caption(session, product.id)
        has_media = await _has_valid_media(session, product.id)
        is_duplicate = product.is_duplicate_of is not None
        is_rejected = product.status == ProductStatus.REJECTED

        result = evaluate_eligibility(
            has_selected_passed_caption=has_caption,
            has_valid_media=has_media,
            is_duplicate=is_duplicate,
            is_rejected=is_rejected,
        )

        score = compute_score(
            has_selected_passed_caption=has_caption,
            has_valid_media=has_media,
            price_present=product.price is not None,
            description=product.description,
            source_message_date=product.source_message_date,
            now=utcnow(),
        )
        product.score = score

        if result.is_eligible:
            product.status = ProductStatus.ELIGIBLE
            await session.commit()
            return EligibilityProcessResult(
                product_id=product.id, outcome="eligible", score=score
            )

        session.add(
            ErrorLog(
                source=ERROR_SOURCE,
                severity=ErrorSeverity.INFO,
                message=(
                    f"Product {product.id} not yet eligible: {' | '.join(result.reasons)}"
                ),
                product_id=product.id,
            )
        )
        await session.commit()
        return EligibilityProcessResult(
            product_id=product.id, outcome="not_eligible", score=score, reasons=result.reasons
        )

    except Exception as exc:  # noqa: BLE001 - isolate one product's failure
        logger.exception("Eligibility evaluation failed for product_id=%s", product.id)
        await session.rollback()
        session.add(
            ErrorLog(
                source=ERROR_SOURCE,
                severity=ErrorSeverity.ERROR,
                message=f"Eligibility evaluation failed for product {product.id}: {exc}",
                product_id=product.id,
            )
        )
        await session.commit()
        return EligibilityProcessResult(product_id=product.id, outcome="failed", error=str(exc))


async def evaluate_pending_products(
    session: AsyncSession, *, limit: int | None = None
) -> list[EligibilityProcessResult]:
    """Evaluate every VALIDATED product (oldest first). Restart-safe: only
    products still at VALIDATED are picked up, so a re-run never
    reprocesses one that already became ELIGIBLE."""
    stmt = select(Product).where(Product.status == ProductStatus.VALIDATED).order_by(Product.id)
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    products = result.scalars().all()

    results: list[EligibilityProcessResult] = []
    for product in products:
        outcome = await evaluate_product(session, product)
        results.append(outcome)
        logger.info(
            "Evaluated product_id=%s outcome=%s score=%s",
            outcome.product_id,
            outcome.outcome,
            outcome.score,
        )
    return results
