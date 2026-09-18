"""
Pure logic for deciding what a channel's next scan should cover.

Deliberately has zero I/O (no DB session, no Telegram client) so it can be
unit-tested exhaustively without a database or network — the business
rule it encodes is exactly the one from the project spec:

- A channel's first-ever scan (no completed INITIAL scan yet) covers only
  the last `initial_scan_days` days.
- Once INITIAL is complete, normal operation is INCREMENTAL: resume from
  `last_processed_message_id`, no date bound needed (the bot never
  stopped, so there's nothing old to worry about).
- If the gap since the last successful scan exceeds `catchup_gap_minutes`
  (the bot was offline for a while), the next scan is CATCHUP: still
  resumes from `last_processed_message_id`, but ALSO bounded to the last
  `initial_scan_days` days — never further back than that, no matter how
  long the bot was down.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.enums import ScanPhase


@dataclass(frozen=True)
class ScanWindow:
    phase: ScanPhase
    min_id: int | None
    since: datetime | None


@dataclass(frozen=True)
class ScanStateSnapshot:
    """The subset of `ScanState` this decision actually depends on.

    A plain snapshot (rather than the ORM object) so tests don't need a
    real `ScanState` row or a DB session to exercise this logic.
    """

    initial_scan_completed_at: datetime | None
    last_processed_message_id: int | None
    last_processed_message_date: datetime | None
    last_success_at: datetime | None
    last_run_at: datetime | None


def determine_scan_window(
    scan_state: ScanStateSnapshot | None,
    now: datetime,
    *,
    initial_scan_days: int,
    catchup_gap_minutes: int,
) -> ScanWindow:
    floor_date = now - timedelta(days=initial_scan_days)

    if scan_state is None or scan_state.initial_scan_completed_at is None:
        return ScanWindow(phase=ScanPhase.INITIAL, min_id=None, since=floor_date)

    gap_reference = scan_state.last_success_at or scan_state.last_run_at
    was_down = gap_reference is None or (now - gap_reference) > timedelta(
        minutes=catchup_gap_minutes
    )

    if was_down:
        since = floor_date
        if scan_state.last_processed_message_date is not None:
            since = max(since, scan_state.last_processed_message_date)
        return ScanWindow(
            phase=ScanPhase.CATCHUP,
            min_id=scan_state.last_processed_message_id,
            since=since,
        )

    return ScanWindow(
        phase=ScanPhase.INCREMENTAL,
        min_id=scan_state.last_processed_message_id,
        since=None,
    )
