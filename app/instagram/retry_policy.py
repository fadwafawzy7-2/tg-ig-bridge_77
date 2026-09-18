"""
Pure exponential-backoff retry policy. Zero dependency (no `app.models`
import — same rationale as `app.queue.scoring`/`app.ai.validator`):
directly unit-testable with plain `python`, no sqlalchemy, no clock
frozen/mocked in a complicated way — `is_due_for_retry` takes `now`
explicitly.

There is no separate "next_retry_at" database column
(`scheduled_posts` already has `attempt_count`/`last_attempt_at`/
`last_error` — see that model's docstring); the delay is recomputed from
those existing columns each time, so no schema change was needed to add
retry scheduling.
"""

from __future__ import annotations

from datetime import datetime


def compute_backoff_seconds(
    attempt_count: int, *, base_delay: float, max_delay: float
) -> float:
    """Delay before attempt number `attempt_count + 1` is allowed, in
    seconds. `attempt_count` is however many attempts have already been
    made (0 before the first attempt). Doubles each attempt, capped at
    `max_delay`."""
    if attempt_count <= 0:
        return 0.0
    delay = base_delay * (2 ** (attempt_count - 1))
    return min(delay, max_delay)


def is_due_for_retry(
    *,
    attempt_count: int,
    last_attempt_at: datetime | None,
    now: datetime,
    base_delay: float,
    max_delay: float,
) -> bool:
    """Whether enough time has passed since the last attempt to try
    again. Always `True` if there has been no attempt yet."""
    if attempt_count <= 0 or last_attempt_at is None:
        return True
    delay = compute_backoff_seconds(attempt_count, base_delay=base_delay, max_delay=max_delay)
    return (now - last_attempt_at).total_seconds() >= delay


def has_attempts_remaining(*, attempt_count: int, max_attempts: int) -> bool:
    return attempt_count < max_attempts
