"""Time-boxed production loop for GitHub Actions.

Runs the same full cycle as worker_once (including manual-capture
dashboard polling, so captions typed via Telegram keep working) on a
short interval, repeatedly, for up to MAX_DURATION_SECONDS. Then it
exits cleanly so the job ends and the *next* scheduled job (triggered
by cron) can start after the gap defined in the workflow schedule.

This intentionally reuses run_once() from worker_once.py rather than
worker.py's run_forever(), because worker.py deliberately excludes
dashboard polling (see its module docstring) — using it here would
silently break manual product capture / caption entry via Telegram.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.core.logging import configure_logging
from app.runtime.worker_once import run_once

logger = logging.getLogger(__name__)

# Leave a safety buffer inside the job's timeout-minutes (checkout, pip
# install, and alembic migrations also consume time from that budget).
MAX_DURATION_SECONDS = 55 * 60

# Gap between internal cycles. Short enough that replies feel near-instant
# while the job is running, but not so short that it opens an excessive
# number of fresh DB connections per hour against the Supabase pooler.
CYCLE_INTERVAL_SECONDS = 15


async def run_timed() -> None:
    start = time.monotonic()
    cycle_count = 0

    while True:
        cycle_count += 1
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Cycle %d failed; continuing until the time budget runs out", cycle_count
            )

        elapsed = time.monotonic() - start
        remaining = MAX_DURATION_SECONDS - elapsed
        if remaining <= CYCLE_INTERVAL_SECONDS:
            logger.info(
                "Time budget reached after %d cycle(s) (%.0fs elapsed); exiting so the job ends",
                cycle_count,
                elapsed,
            )
            break

        await asyncio.sleep(CYCLE_INTERVAL_SECONDS)


async def main() -> None:
    configure_logging()
    await run_timed()


if __name__ == "__main__":
    asyncio.run(main())
