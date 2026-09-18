"""Long-running production worker for the complete Telegram -> Instagram flow.

Ingestion is manual: items reach the pipeline via the dashboard bot's
forward-to-capture flow (app/telegram/manual_capture.py), not by scanning
channels — there is no Telethon/api_id/api_hash dependency anywhere in
this process.

This process only runs the pipeline stages below (parsing through
publishing). Dashboard bot polling (captures, button presses) is a
SEPARATE, dedicated process — `python -m app.dashboard.bot` (the
`dashboard` service in docker-compose.yml) — deliberately kept out of
this loop, since running it here too would mean two processes racing
over the same Telegram getUpdates offset.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.ai.ai_client import GroqClient
from app.ai.caption_runner import generate_pending_captions
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.instagram.client import GraphAPIInstagramClient
from app.instagram.runner import PublisherRunSummary, run_publisher
from app.media.retention import RetentionSummary, purge_expired_media
from app.parsing.parser_runner import ParserRunSummary, process_pending_messages
from app.queue.runner import PipelineSummary, run_pipeline
from app.queue.scheduler import StoryScheduleSummary, ensure_default_daily_limits, schedule_stories
from app.queue.time_utils import today_in_timezone
from app.runtime.preflight import assert_runtime_ready

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerCycleSummary:
    parser: ParserRunSummary
    captions: object
    pipeline: PipelineSummary
    stories: StoryScheduleSummary
    publisher: PublisherRunSummary
    retention: RetentionSummary


async def run_cycle(*, ai_client: GroqClient, instagram_client: GraphAPIInstagramClient) -> WorkerCycleSummary:
    """Run every stage in dependency order in one DB session per stage.

    Stage failures are isolated by the worker loop; a single bad provider
    or row is recorded by the underlying phase service and does not stop
    future cycles.
    """
    settings = get_settings()

    async with AsyncSessionLocal() as session:
        await ensure_default_daily_limits(session, today_in_timezone(settings.SCHEDULER_TIMEZONE))

    async with AsyncSessionLocal() as session:
        parser_summary = await process_pending_messages(session)

    async with AsyncSessionLocal() as session:
        caption_summary = await generate_pending_captions(session, ai_client)

    async with AsyncSessionLocal() as session:
        pipeline_summary = await run_pipeline(session)

    async with AsyncSessionLocal() as session:
        story_summary = await schedule_stories(session)

    async with AsyncSessionLocal() as session:
        publisher_summary = await run_publisher(session, instagram_client)

    async with AsyncSessionLocal() as session:
        retention_summary = await purge_expired_media(session)

    return WorkerCycleSummary(
        parser=parser_summary,
        captions=caption_summary,
        pipeline=pipeline_summary,
        stories=story_summary,
        publisher=publisher_summary,
        retention=retention_summary,
    )


async def run_forever() -> None:
    settings = get_settings()
    assert_runtime_ready(settings)

    ai_client = GroqClient.from_settings()
    instagram_client = GraphAPIInstagramClient.from_settings()

    interval = max(5, int(settings.WORKER_INTERVAL_SECONDS))
    while True:
        started = asyncio.get_running_loop().time()
        try:
            summary = await run_cycle(ai_client=ai_client, instagram_client=instagram_client)
            logger.info(
                "Worker cycle complete: parsed=%d captions=%d scheduled=%d stories=%d published=%d media_purged=%d",
                summary.parser.messages_processed,
                summary.captions.products_processed,
                summary.pipeline.newly_scheduled,
                summary.stories.created,
                summary.publisher.published,
                summary.retention.media_files_deleted,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker cycle failed; continuing after interval")
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.0, interval - elapsed))


async def main() -> None:
    from app.core.logging import configure_logging

    configure_logging()
    await run_forever()


if __name__ == "__main__":
    asyncio.run(main())
