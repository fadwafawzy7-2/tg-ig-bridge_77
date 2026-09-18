"""
ELIGIBLE -> QUEUED.

Deliberately thin: by the time a product is ELIGIBLE, `eligibility_service.py`
has already run the gate and stored a score. "Queueing" here is just the
status flip that makes the product visible to `scheduler.py`'s query —
there is no separate queue table (see `app/queue/__init__.py`'s module
docstring: `products.status='QUEUED'`, ordered by `products.score DESC`
at read time, IS the queue; a dedicated table would just duplicate what
the status column + an index already give us for free, so no migration
was added for one).

Idempotent by construction: this only ever selects products currently at
ELIGIBLE, so running it twice in a row (e.g. after a crash) is a no-op
the second time — nothing to re-queue, nothing double-counted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ProductStatus
from app.models.product import Product

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueueResult:
    product_id: int
    outcome: str = "queued"


async def enqueue_eligible_products(
    session: AsyncSession, *, limit: int | None = None
) -> list[QueueResult]:
    """Move every ELIGIBLE product to QUEUED, ordered by score (highest
    first) so the highest-scoring products flip status first — the order
    scheduled_posts get created in later (`scheduler.py`) follows the same
    ordering again at read time, so this ordering here mostly matters for
    `limit`-bounded partial runs."""
    stmt = (
        select(Product)
        .where(Product.status == ProductStatus.ELIGIBLE)
        .order_by(Product.score.desc().nullslast(), Product.id.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    products = result.scalars().all()

    results: list[QueueResult] = []
    for product in products:
        product.status = ProductStatus.QUEUED
        results.append(QueueResult(product_id=product.id))

    await session.commit()

    for r in results:
        logger.info("Queued product_id=%s", r.product_id)

    return results
