"""
Batch driver: run caption generation + validation for every PARSED
`products` row (`caption_service.py`).

Like `app.parsing.parser_runner`, this deliberately has no periodic-
execution machinery — `generate_pending_captions()` is what a scheduler
would call later; wiring that up is Phase 6+ territory, not started here.

Run manually with:
    python -m app.ai.caption_runner
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ai_client import AIClient
from app.ai.caption_service import CaptionProcessResult, process_product
from app.models.enums import ProductStatus
from app.models.product import Product

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptionRunSummary:
    results: list[CaptionProcessResult]

    @property
    def products_processed(self) -> int:
        return len(self.results)

    @property
    def selected(self) -> int:
        return sum(1 for r in self.results if r.outcome == "selected")

    @property
    def all_rejected(self) -> int:
        return sum(1 for r in self.results if r.outcome == "all_rejected")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "failed")

    @property
    def no_source_text(self) -> int:
        return sum(1 for r in self.results if r.outcome == "no_source_text")


async def generate_pending_captions(
    session: AsyncSession,
    ai_client: AIClient,
    limit: int | None = None,
) -> CaptionRunSummary:
    """Process every PARSED product (oldest first) that is NOT a manual
    capture awaiting its own admin-written caption — see
    `Product.preferred_content_type`'s docstring. Restart-safe: only
    products still at PARSED are picked up, so re-running after a crash
    never reprocesses a product that already got a selected caption
    (status moved on to VALIDATED) — and safely retries ones that
    previously failed or had every candidate rejected (status stays
    PARSED for those, by design — see `caption_service.py`).
    """
    stmt = (
        select(Product)
        .where(Product.status == ProductStatus.PARSED)
        .where(Product.preferred_content_type.is_(None))
        .order_by(Product.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    products = result.scalars().all()

    results: list[CaptionProcessResult] = []
    for product in products:
        outcome = await process_product(session, product, ai_client)
        results.append(outcome)
        logger.info(
            "Captioned product_id=%s outcome=%s selected_caption_id=%s candidates=%d",
            outcome.product_id,
            outcome.outcome,
            outcome.selected_caption_id,
            outcome.candidate_count,
        )

    return CaptionRunSummary(results=results)


async def _main() -> None:  # pragma: no cover - manual/CLI entrypoint
    from app.ai.ai_client import GroqClient
    from app.core.logging import configure_logging
    from app.db.session import AsyncSessionLocal

    configure_logging()
    ai_client = GroqClient.from_settings()

    async with AsyncSessionLocal() as session:
        summary = await generate_pending_captions(session, ai_client)
        logger.info(
            "Caption run complete: %d processed (%d selected, %d all-rejected, "
            "%d failed, %d skipped for no source text)",
            summary.products_processed,
            summary.selected,
            summary.all_rejected,
            summary.failed,
            summary.no_source_text,
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
