"""
Batch driver: process every PENDING `source_messages` row through the
parser/duplicate-detection pipeline (`product_service.py`).

Like `app.telegram.monitor_runner`, this deliberately has no periodic-
execution machinery — `process_pending_messages()` is what a scheduler
(cron/APScheduler/systemd timer) would call later; wiring that up is not
part of this phase.

Run manually with:
    python -m app.parsing.parser_runner
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SourceMessageStatus
from app.models.source_message import SourceMessage
from app.parsing.product_service import ProcessResult, process_source_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParserRunSummary:
    results: list[ProcessResult]

    @property
    def messages_processed(self) -> int:
        return len(self.results)

    @property
    def created(self) -> int:
        return sum(1 for r in self.results if r.outcome == "created")

    @property
    def duplicates_linked(self) -> int:
        return sum(1 for r in self.results if r.outcome == "duplicate_linked")

    @property
    def ignored(self) -> int:
        return sum(1 for r in self.results if r.outcome == "ignored")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "failed")


async def process_pending_messages(
    session: AsyncSession, limit: int | None = None
) -> ParserRunSummary:
    """Process every PENDING source_message (oldest first). Restart-safe:
    only PENDING rows are picked up, so re-running after a crash never
    reprocesses already-handled messages (PROCESSED/IGNORED/FAILED)."""
    stmt = (
        select(SourceMessage)
        .where(SourceMessage.status == SourceMessageStatus.PENDING)
        .order_by(SourceMessage.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    messages = result.scalars().all()

    results: list[ProcessResult] = []
    for message in messages:
        outcome = await process_source_message(session, message)
        results.append(outcome)
        logger.info(
            "Processed source_message_id=%s outcome=%s product_id=%s",
            outcome.source_message_id,
            outcome.outcome,
            outcome.product_id,
        )

    return ParserRunSummary(results=results)


async def _main() -> None:  # pragma: no cover - manual/CLI entrypoint
    from app.core.logging import configure_logging
    from app.db.session import AsyncSessionLocal

    configure_logging()

    async with AsyncSessionLocal() as session:
        summary = await process_pending_messages(session)
        logger.info(
            "Parser run complete: %d processed (%d created, %d linked as "
            "duplicates, %d ignored, %d failed)",
            summary.messages_processed,
            summary.created,
            summary.duplicates_linked,
            summary.ignored,
            summary.failed,
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
