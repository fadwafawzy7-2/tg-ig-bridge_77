"""One-shot production cycle for GitHub Actions / cron execution.

Ingestion is manual: items reach the pipeline via the dashboard bot's
forward-to-capture flow (app/telegram/manual_capture.py) — there is no
Telethon/api_id/api_hash dependency anywhere in this process.
"""
from __future__ import annotations

import asyncio
import logging

from app.ai.ai_client import GroqClient
from app.ai.caption_runner import generate_pending_captions
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionLocal
from app.dashboard.bot import Dashboard
from app.instagram.client import GraphAPIInstagramClient
from app.instagram.runner import run_publisher
from app.media.retention import purge_expired_media
from app.parsing.parser_runner import process_pending_messages
from app.queue.runner import run_pipeline
from app.runtime.preflight import assert_runtime_ready
from app.queue.time_utils import today_in_timezone
from app.queue.scheduler import ensure_default_daily_limits, schedule_stories

logger = logging.getLogger(__name__)


async def run_once() -> None:
    settings = get_settings()
    assert_runtime_ready(settings)

    # Process Telegram dashboard updates first (captures, button presses).
    # One failed update must never block the rest of this cycle
    # (captions/scheduling/publishing) — same fault-isolation principle as
    # every other stage below, and every batch driver elsewhere in this
    # codebase.
    dashboard = Dashboard()
    try:
        await dashboard.run_once()
    except Exception:
        logger.exception("Dashboard update processing failed; continuing with the rest of the cycle")
    finally:
        await dashboard.close()

    async with AsyncSessionLocal() as session:
        await ensure_default_daily_limits(session, today_in_timezone(settings.SCHEDULER_TIMEZONE))

    ai_client = GroqClient.from_settings()
    instagram_client = GraphAPIInstagramClient.from_settings()

    async with AsyncSessionLocal() as session:
        parser = await process_pending_messages(session)
    async with AsyncSessionLocal() as session:
        captions = await generate_pending_captions(session, ai_client)
    async with AsyncSessionLocal() as session:
        pipeline = await run_pipeline(session)
    async with AsyncSessionLocal() as session:
        stories = await schedule_stories(session)
    async with AsyncSessionLocal() as session:
        publisher = await run_publisher(session, instagram_client)
    async with AsyncSessionLocal() as session:
        retention = await purge_expired_media(session)

    logger.info(
        "One-shot complete: parsed=%d captions=%d scheduled=%d stories=%d published=%d media_purged=%d",
        parser.messages_processed,
        captions.products_processed,
        pipeline.newly_scheduled,
        stories.created,
        publisher.published,
        retention.media_files_deleted,
    )


async def main() -> None:
    configure_logging()
    await run_once()


if __name__ == "__main__":
    asyncio.run(main())
