"""
Tests for `app.instagram.retry_policy`.

Pure logic, zero dependency — run with plain `python`/`pytest`, no
database, no sqlalchemy import.
"""

from datetime import datetime, timedelta, timezone

from app.instagram.retry_policy import (
    compute_backoff_seconds,
    has_attempts_remaining,
    is_due_for_retry,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_zero_attempts_means_no_delay():
    assert compute_backoff_seconds(0, base_delay=30, max_delay=3600) == 0.0


def test_backoff_doubles_each_attempt():
    assert compute_backoff_seconds(1, base_delay=30, max_delay=3600) == 30.0
    assert compute_backoff_seconds(2, base_delay=30, max_delay=3600) == 60.0
    assert compute_backoff_seconds(3, base_delay=30, max_delay=3600) == 120.0
    assert compute_backoff_seconds(4, base_delay=30, max_delay=3600) == 240.0


def test_backoff_is_capped_at_max_delay():
    assert compute_backoff_seconds(20, base_delay=30, max_delay=3600) == 3600.0


def test_never_attempted_is_always_due():
    assert is_due_for_retry(
        attempt_count=0, last_attempt_at=None, now=NOW, base_delay=30, max_delay=3600
    ) is True


def test_not_due_before_backoff_elapses():
    result = is_due_for_retry(
        attempt_count=1,
        last_attempt_at=NOW - timedelta(seconds=10),
        now=NOW,
        base_delay=30,
        max_delay=3600,
    )
    assert result is False


def test_due_after_backoff_elapses():
    result = is_due_for_retry(
        attempt_count=1,
        last_attempt_at=NOW - timedelta(seconds=31),
        now=NOW,
        base_delay=30,
        max_delay=3600,
    )
    assert result is True


def test_due_exactly_at_boundary_is_due():
    result = is_due_for_retry(
        attempt_count=1,
        last_attempt_at=NOW - timedelta(seconds=30),
        now=NOW,
        base_delay=30,
        max_delay=3600,
    )
    assert result is True


def test_higher_attempt_count_needs_longer_wait():
    just_over_first_backoff = NOW - timedelta(seconds=31)
    # after 1 attempt, 31s is enough
    assert is_due_for_retry(
        attempt_count=1, last_attempt_at=just_over_first_backoff, now=NOW, base_delay=30, max_delay=3600
    ) is True
    # after 2 attempts, the same 31s elapsed is NOT enough (needs 60s)
    assert is_due_for_retry(
        attempt_count=2, last_attempt_at=just_over_first_backoff, now=NOW, base_delay=30, max_delay=3600
    ) is False


def test_has_attempts_remaining():
    assert has_attempts_remaining(attempt_count=0, max_attempts=5) is True
    assert has_attempts_remaining(attempt_count=4, max_attempts=5) is True
    assert has_attempts_remaining(attempt_count=5, max_attempts=5) is False
    assert has_attempts_remaining(attempt_count=6, max_attempts=5) is False
