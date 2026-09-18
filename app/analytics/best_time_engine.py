"""
Pure Best-Time Engine for Asia/Gaza. Zero dependency (no `app.models`
import) — takes plain `EngagementSample`s already converted to local time
by the caller (`analytics_service.py`), and never reads a clock or
timezone itself, so it's directly unit-testable with plain `python`, no
sqlalchemy.

The rule this module exists to enforce: NEVER claim a "best time" is
learned/proven from too little data. Below `MIN_TOTAL_SAMPLES` usable
observations (or below `MIN_SAMPLES_PER_HOUR` for any individual hour),
this returns `is_learned=False` and a fallback to the EXISTING default
scheduling window (passed in by the caller — `analytics_service.py`
sources it from `app.queue.scheduler`'s own constants, imported
read-only, never modified here) — worded and flagged as a fallback, not
a recommendation. `notes` always spells this out in plain language so a
caller can't accidentally present a fallback as a proven result.

"Learn gradually" is implemented as a pure recompute-from-all-available-
data aggregation (mean engagement per hour bucket), not an online/ML
model with persisted state: every call reflects exactly the dataset
passed to it, so as more published posts accumulate real analytics over
time, the next call naturally reflects that — no separate "training
step", no model file, nothing to go stale.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from app.analytics.performance import EngagementSample

# Below this many usable engagement samples for a given hour bucket, its
# average is not considered reliable enough to rank/recommend from.
MIN_SAMPLES_PER_HOUR = 3

# Below this many TOTAL usable samples across the whole grouping, no
# per-hour ranking is attempted at all - fall back to the default window
# outright rather than ranking on statistical noise.
MIN_TOTAL_SAMPLES = 5


@dataclass(frozen=True)
class HourRank:
    hour: int
    sample_count: int
    mean_engagement: Decimal


@dataclass(frozen=True)
class WeekdayRank:
    weekday: int  # 0=Monday ... 6=Sunday
    sample_count: int
    mean_engagement: Decimal


@dataclass(frozen=True)
class BestTimeResult:
    is_learned: bool
    sample_count: int
    ranked_hours: tuple[HourRank, ...]
    ranked_weekdays: tuple[WeekdayRank, ...]
    fallback_window: tuple[int, int]
    notes: str

    @property
    def best_hour(self) -> int | None:
        """The single top-ranked hour, or `None` if nothing was learned
        (callers needing a concrete hour to act on should fall back to
        the midpoint of `fallback_window` in that case, not treat this
        as 0)."""
        return self.ranked_hours[0].hour if self.ranked_hours else None


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _insufficient_data_result(
    *, total: int, fallback_window: tuple[int, int], reason: str, weekday_ranks: tuple = ()
) -> BestTimeResult:
    return BestTimeResult(
        is_learned=False,
        sample_count=total,
        ranked_hours=(),
        ranked_weekdays=weekday_ranks,
        fallback_window=fallback_window,
        notes=reason,
    )


def compute_best_times(
    samples: list[EngagementSample],
    *,
    fallback_window: tuple[int, int],
    min_samples_per_hour: int = MIN_SAMPLES_PER_HOUR,
    min_total_samples: int = MIN_TOTAL_SAMPLES,
) -> BestTimeResult:
    """The core Best-Time computation. Always returns a result — never
    raises for "not enough data"; that's an ordinary, expected outcome
    (`is_learned=False`), not an error."""
    total = len(samples)

    if total < min_total_samples:
        return _insufficient_data_result(
            total=total,
            fallback_window=fallback_window,
            reason=(
                f"Only {total} usable engagement sample(s) available (need at least "
                f"{min_total_samples}) - using the existing default scheduling window "
                f"{fallback_window[0]}:00-{fallback_window[1]}:00 (Asia/Gaza) instead of a "
                "learned recommendation. This is a fallback, not a claim that this window "
                "performs best."
            ),
        )

    by_hour: dict[int, list[Decimal]] = defaultdict(list)
    by_weekday: dict[int, list[Decimal]] = defaultdict(list)
    for s in samples:
        by_hour[s.local_hour].append(s.engagement)
        by_weekday[s.local_weekday].append(s.engagement)

    hour_ranks = [
        HourRank(hour=h, sample_count=len(vals), mean_engagement=_mean(vals))
        for h, vals in by_hour.items()
        if len(vals) >= min_samples_per_hour
    ]
    hour_ranks.sort(key=lambda hr: hr.mean_engagement, reverse=True)

    weekday_ranks = [
        WeekdayRank(weekday=w, sample_count=len(vals), mean_engagement=_mean(vals))
        for w, vals in by_weekday.items()
        if len(vals) >= min_samples_per_hour
    ]
    weekday_ranks.sort(key=lambda wr: wr.mean_engagement, reverse=True)

    if not hour_ranks:
        return _insufficient_data_result(
            total=total,
            fallback_window=fallback_window,
            reason=(
                f"{total} total sample(s) available, but no single hour has reached "
                f"{min_samples_per_hour} sample(s) yet - using the existing default "
                f"scheduling window {fallback_window[0]}:00-{fallback_window[1]}:00 "
                "(Asia/Gaza) instead of a learned hour recommendation."
            ),
            weekday_ranks=tuple(weekday_ranks),
        )

    return BestTimeResult(
        is_learned=True,
        sample_count=total,
        ranked_hours=tuple(hour_ranks),
        ranked_weekdays=tuple(weekday_ranks),
        fallback_window=fallback_window,
        notes=(
            f"Learned from {total} usable engagement sample(s) across "
            f"{len(hour_ranks)} hour bucket(s) with >= {min_samples_per_hour} samples each."
        ),
    )
