"""
Top-level entrypoint for one Telegram monitoring pass: scan every ACTIVE
channel and report what happened.

Deliberately does NOT include any periodic-execution machinery (cron,
APScheduler, systemd timer, ...) — that's out of scope for Phase 3 (and
distinct from the future publishing "Scheduler" anyway). `run_monitor_once()`
is the function such a trigger would call; wiring up *when* it runs is a
later concern.

Run manually with:
    python -m app.telegram.monitor_runner
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import ChannelStatus
from app.telegram.channel_service import list_channels
from app.telegram.client import TelegramClient
from app.telegram.scan_service import ScanResult, scan_channel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorRunSummary:
    results: list[ScanResult]

    @property
    def channels_scanned(self) -> int:
        return len(self.results)

    @property
    def channels_failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def total_messages_ingested(self) -> int:
        return sum(r.messages_ingested for r in self.results)


async def run_monitor_once(session: AsyncSession, client: TelegramClient) -> MonitorRunSummary:
    """Scan every ACTIVE channel once. One channel's failure never stops
    the others — `scan_channel()` already isolates errors per channel."""
    channels = await list_channels(session, status=ChannelStatus.ACTIVE)

    results: list[ScanResult] = []
    for channel in channels:
        result = await scan_channel(
            session,
            client,
            channel,
            initial_scan_days=settings.TELEGRAM_INITIAL_SCAN_DAYS,
            catchup_gap_minutes=settings.TELEGRAM_CATCHUP_GAP_MINUTES,
        )
        results.append(result)
        logger.info(
            "Scanned channel_id=%s phase=%s messages=%d ok=%s",
            result.channel_id,
            result.phase,
            result.messages_ingested,
            result.ok,
        )

    return MonitorRunSummary(results=results)


async def _main() -> None:  # pragma: no cover - manual/CLI entrypoint
    from app.core.logging import configure_logging
    from app.db.session import AsyncSessionLocal
    from app.telegram.client import TelethonClient

    configure_logging()

    client = TelethonClient(
        api_id=settings.TELEGRAM_API_ID,
        api_hash=settings.TELEGRAM_API_HASH,
        session_name=settings.TELEGRAM_SESSION_NAME,
    )
    await client.connect()
    try:
        async with AsyncSessionLocal() as session:
            summary = await run_monitor_once(session, client)
            logger.info(
                "Monitor run complete: %d channels, %d failed, %d messages ingested",
                summary.channels_scanned,
                summary.channels_failed,
                summary.total_messages_ingested,
            )
    finally:
        await client.disconnect()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
