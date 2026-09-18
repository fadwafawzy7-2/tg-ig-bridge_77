"""
Orchestrates one scan pass of a single channel: figure out what window to
scan (`scan_window.py`), pull messages from Telegram (`TelegramClient`),
persist them (`ingestion_service.py`), and update `scan_state` so the next
run resumes correctly — restart-safe by construction, since every message
is committed as it's ingested and `scan_state` is only updated after the
whole pass succeeds.

Errors are caught here (not raised) and recorded in the `errors` table:
one broken channel must never stop the rest of the monitor run.
"""

from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.models.enums import ErrorSeverity, ScanPhase
from app.models.error_log import ErrorLog
from app.models.media import Media
from app.core.config import settings
from pathlib import Path
from app.models.scan_state import ScanState
from app.media.cloud_storage import upload_file
from app.telegram.client import TelegramClient
from app.telegram.ingestion_service import ingest_message
from app.telegram.scan_window import ScanStateSnapshot, ScanWindow, determine_scan_window

logger = logging.getLogger(__name__)

ERROR_SOURCE = "telegram_monitor"


@dataclass(frozen=True)
class ScanResult:
    channel_id: int
    phase: ScanPhase
    messages_ingested: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def _get_or_create_scan_state(session: AsyncSession, channel: Channel) -> ScanState:
    if channel.scan_state is not None:
        return channel.scan_state
    scan_state = ScanState(channel_id=channel.id)
    session.add(scan_state)
    await session.flush()
    channel.scan_state = scan_state
    return scan_state


def _snapshot(scan_state: ScanState) -> ScanStateSnapshot:
    return ScanStateSnapshot(
        initial_scan_completed_at=scan_state.initial_scan_completed_at,
        last_processed_message_id=scan_state.last_processed_message_id,
        last_processed_message_date=scan_state.last_processed_message_date,
        last_success_at=scan_state.last_success_at,
        last_run_at=scan_state.last_run_at,
    )


async def scan_channel(
    session: AsyncSession,
    client: TelegramClient,
    channel: Channel,
    *,
    initial_scan_days: int,
    catchup_gap_minutes: int,
) -> ScanResult:
    now = datetime.now(timezone.utc)
    scan_state = await _get_or_create_scan_state(session, channel)

    window: ScanWindow = determine_scan_window(
        _snapshot(scan_state),
        now,
        initial_scan_days=initial_scan_days,
        catchup_gap_minutes=catchup_gap_minutes,
    )

    messages_ingested = 0
    last_message_id = scan_state.last_processed_message_id
    last_message_date = scan_state.last_processed_message_date

    try:
        async for message in client.iter_messages(
            channel.telegram_channel_id, min_id=window.min_id, since=window.since
        ):
            source_message = await ingest_message(session, channel, message)
            downloader = getattr(client, "download_message_media", None)
            if downloader is not None and message.media:
                target_dir = Path(settings.MEDIA_STORAGE_DIR) / str(channel.id) / str(message.telegram_message_id)
                downloaded = await downloader(
                    channel.telegram_channel_id,
                    message.telegram_message_id,
                    str(target_dir),
                )
                for source_media_id, absolute_path in downloaded:
                    media_row = (await session.execute(
                        select(Media).where(
                            Media.source_message_id == source_message.id,
                            Media.telegram_file_id == source_media_id,
                        )
                    )).scalar_one_or_none()
                    if media_row is not None:
                        relative = str(Path(absolute_path).resolve().relative_to(Path(settings.MEDIA_STORAGE_DIR).resolve()))
                        if settings.MEDIA_STORAGE_BACKEND.lower() in ("s3", "r2"):
                            object_key = f"telegram/{channel.id}/{message.telegram_message_id}/{source_media_id}/{Path(absolute_path).name}"
                            media_row.file_path = await asyncio.to_thread(upload_file, absolute_path, object_key)
                        else:
                            media_row.file_path = relative
                await session.commit()
            messages_ingested += 1
            if last_message_id is None or message.telegram_message_id > last_message_id:
                last_message_id = message.telegram_message_id
                last_message_date = message.message_date
    except Exception as exc:  # noqa: BLE001 - deliberately broad: one bad
        # channel must never take down the rest of the monitor run.
        logger.exception("Scan failed for channel_id=%s", channel.id)
        scan_state.last_run_at = now
        scan_state.consecutive_failures += 1
        session.add(
            ErrorLog(
                source=ERROR_SOURCE,
                severity=ErrorSeverity.ERROR,
                message=f"Scan failed for channel {channel.id}: {exc}",
                channel_id=channel.id,
            )
        )
        await session.commit()
        return ScanResult(
            channel_id=channel.id, phase=window.phase, messages_ingested=messages_ingested,
            error=str(exc),
        )

    scan_state.last_processed_message_id = last_message_id
    scan_state.last_processed_message_date = last_message_date
    scan_state.last_run_at = now
    scan_state.last_success_at = now
    scan_state.consecutive_failures = 0

    if window.phase is ScanPhase.INITIAL:
        scan_state.initial_scan_started_at = scan_state.initial_scan_started_at or now
        scan_state.initial_scan_completed_at = now
        scan_state.scan_phase = ScanPhase.INCREMENTAL
    elif window.phase is ScanPhase.CATCHUP:
        scan_state.scan_phase = ScanPhase.INCREMENTAL
    else:
        scan_state.scan_phase = ScanPhase.INCREMENTAL

    await session.commit()

    return ScanResult(
        channel_id=channel.id, phase=window.phase, messages_ingested=messages_ingested
    )
