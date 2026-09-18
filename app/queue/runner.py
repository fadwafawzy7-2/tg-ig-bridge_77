"""
Batch driver stitching the three automatic Phase 6 stages together in
order: `eligibility_service.evaluate_pending_products` (VALIDATED ->
ELIGIBLE) -> `queue_service.enqueue_eligible_products` (ELIGIBLE ->
QUEUED) -> `scheduler.run_scheduler` (QUEUED -> SCHEDULED).

Like `app.ai.caption_runner`, this has no periodic-execution machinery of
its own — `run_pipeline()` is what a scheduler/cron would call
repeatedly; wiring that up is later-phase territory.

Run manually with:
    python -m app.queue.runner
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.queue.eligibility_service import EligibilityProcessResult, evaluate_pending_products
from app.queue.queue_service import QueueResult, enqueue_eligible_products
from app.queue.scheduler import ScheduleResult, run_scheduler

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineSummary:
    eligibility_results: list[EligibilityProcessResult]
    queue_results: list[QueueResult]
    schedule_results: list[ScheduleResult]

    @property
    def newly_eligible(self) -> int:
        return sum(1 for r in self.eligibility_results if r.outcome == "eligible")

    @property
    def newly_queued(self) -> int:
        return len(self.queue_results)

    @property
    def newly_scheduled(self) -> int:
        return sum(1 for r in self.schedule_results if r.outcome == "scheduled")


async def run_pipeline(session: AsyncSession) -> PipelineSummary:
    eligibility_results = await evaluate_pending_products(session)
    queue_results = await enqueue_eligible_products(session)
    schedule_results = await run_scheduler(session)

    return PipelineSummary(
        eligibility_results=eligibility_results,
        queue_results=queue_results,
        schedule_results=schedule_results,
    )


async def _main() -> None:  # pragma: no cover - manual/CLI entrypoint
    from app.core.logging import configure_logging
    from app.db.session import AsyncSessionLocal

    configure_logging()

    async with AsyncSessionLocal() as session:
        summary = await run_pipeline(session)
        logger.info(
            "Phase 6 pipeline run complete: %d newly eligible, %d newly queued, %d newly scheduled",
            summary.newly_eligible,
            summary.newly_queued,
            summary.newly_scheduled,
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
