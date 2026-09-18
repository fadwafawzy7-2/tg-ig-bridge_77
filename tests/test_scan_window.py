"""
Unit tests for `app.telegram.scan_window.determine_scan_window`.

These need NO database and NO Telegram connection — `ScanStateSnapshot`
is a plain dataclass, so this is the one Phase 3 test module that can run
in any environment, including this project's CI before a database is
provisioned.
"""

from datetime import datetime, timedelta, timezone

from app.models.enums import ScanPhase
from app.telegram.scan_window import ScanStateSnapshot, determine_scan_window

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
INITIAL_DAYS = 5
CATCHUP_GAP_MINUTES = 30


def test_brand_new_channel_gets_initial_phase_bounded_to_last_n_days():
    window = determine_scan_window(
        None, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.INITIAL
    assert window.min_id is None
    assert window.since == NOW - timedelta(days=INITIAL_DAYS)


def test_channel_with_scan_state_but_no_completed_initial_scan_stays_initial():
    """E.g. the previous INITIAL attempt crashed partway through."""
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=None,
        last_processed_message_id=None,
        last_processed_message_date=None,
        last_success_at=None,
        last_run_at=NOW - timedelta(minutes=5),
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.INITIAL
    assert window.since == NOW - timedelta(days=INITIAL_DAYS)


def test_recently_active_channel_gets_plain_incremental():
    """Bot has been running continuously — no date bound needed at all."""
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=NOW - timedelta(days=10),
        last_processed_message_id=555,
        last_processed_message_date=NOW - timedelta(days=8),  # old, but irrelevant here
        last_success_at=NOW - timedelta(minutes=5),
        last_run_at=NOW - timedelta(minutes=5),
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.INCREMENTAL
    assert window.min_id == 555
    assert window.since is None


def test_channel_offline_past_the_gap_threshold_triggers_catchup():
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=NOW - timedelta(days=10),
        last_processed_message_id=555,
        last_processed_message_date=NOW - timedelta(hours=2),
        last_success_at=NOW - timedelta(hours=2),
        last_run_at=NOW - timedelta(hours=2),
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.CATCHUP
    assert window.min_id == 555


def test_catchup_is_never_bounded_further_back_than_n_days_even_after_long_downtime():
    """Bot was down for 3 weeks — catch-up must still cap at the last 5
    days, not backfill the whole outage."""
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=NOW - timedelta(days=60),
        last_processed_message_id=100,
        last_processed_message_date=NOW - timedelta(weeks=3),
        last_success_at=NOW - timedelta(weeks=3),
        last_run_at=NOW - timedelta(weeks=3),
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.CATCHUP
    assert window.since == NOW - timedelta(days=INITIAL_DAYS)


def test_catchup_uses_last_processed_date_when_more_recent_than_the_floor():
    """Bot was down for only 45 minutes (past the 30-minute threshold, so
    still CATCHUP) — the window should start from the last processed
    message, not jump all the way back to the 5-day floor."""
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=NOW - timedelta(days=10),
        last_processed_message_id=900,
        last_processed_message_date=NOW - timedelta(minutes=45),
        last_success_at=NOW - timedelta(minutes=45),
        last_run_at=NOW - timedelta(minutes=45),
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.CATCHUP
    assert window.since == NOW - timedelta(minutes=45)


def test_gap_is_measured_from_last_success_not_last_run():
    """A channel that keeps failing (last_run_at advances every attempt,
    last_success_at doesn't) must still be treated as needing catch-up,
    not incorrectly read as healthy just because it *tried* recently."""
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=NOW - timedelta(days=10),
        last_processed_message_id=42,
        last_processed_message_date=NOW - timedelta(hours=5),
        last_success_at=NOW - timedelta(hours=5),
        last_run_at=NOW - timedelta(minutes=1),  # tried again very recently, but failed
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.CATCHUP


def test_gap_falls_back_to_last_run_when_never_succeeded_after_initial():
    """Edge case: initial scan completed, but every scan since has failed
    (last_success_at is still None). Must not crash and must treat this
    as needing catch-up."""
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=NOW - timedelta(days=10),
        last_processed_message_id=None,
        last_processed_message_date=None,
        last_success_at=None,
        last_run_at=NOW - timedelta(hours=1),
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.CATCHUP
    assert window.since == NOW - timedelta(days=INITIAL_DAYS)


def test_gap_exactly_at_threshold_is_not_yet_catchup():
    """Boundary check: a gap strictly greater than the threshold triggers
    catch-up; exactly at the threshold does not."""
    snapshot = ScanStateSnapshot(
        initial_scan_completed_at=NOW - timedelta(days=10),
        last_processed_message_id=7,
        last_processed_message_date=NOW - timedelta(minutes=CATCHUP_GAP_MINUTES),
        last_success_at=NOW - timedelta(minutes=CATCHUP_GAP_MINUTES),
        last_run_at=NOW - timedelta(minutes=CATCHUP_GAP_MINUTES),
    )
    window = determine_scan_window(
        snapshot, NOW, initial_scan_days=INITIAL_DAYS, catchup_gap_minutes=CATCHUP_GAP_MINUTES
    )

    assert window.phase is ScanPhase.INCREMENTAL
