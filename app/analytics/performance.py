"""
Pure descriptive performance breakdown — by hour of day, day of week, and
content type. Zero dependency, same rationale as `metrics.py`.

This is REPORTING, not a recommendation: every bucket is returned along
with its own `sample_count`, however small (even 1), so callers can judge
reliability for themselves — nothing here is hidden or gated by a minimum
sample size. (Contrast with `best_time_engine.py`, which DOES apply a
minimum-sample confidence gate before it will call anything "learned",
because that module makes an actionable recommendation, not just a
report.)

Local hour/weekday must already be computed by the caller in Asia/Gaza
time (`analytics_service.py` does this) — this module never reads a
clock or a timezone itself.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class EngagementSample:
    local_hour: int  # 0-23, already converted to local time by the caller
    local_weekday: int  # 0=Monday ... 6=Sunday (Python's date.weekday())
    content_type: str
    engagement: Decimal


@dataclass(frozen=True)
class BucketStats:
    key: int | str
    sample_count: int
    mean_engagement: Decimal


@dataclass(frozen=True)
class PerformanceBreakdown:
    total_samples: int
    by_hour: tuple[BucketStats, ...]
    by_weekday: tuple[BucketStats, ...]
    by_content_type: tuple[BucketStats, ...]


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _bucket_stats(grouped: dict) -> list[BucketStats]:
    return [
        BucketStats(key=key, sample_count=len(vals), mean_engagement=_mean(vals))
        for key, vals in grouped.items()
    ]


def compute_breakdown(samples: list[EngagementSample]) -> PerformanceBreakdown:
    """Group `samples` (already filtered to real, usable engagement
    values by the caller — see `metrics.effective_engagement`) by hour,
    weekday, and content type. An empty `samples` list produces an empty,
    honestly-labeled breakdown (`total_samples=0`), never fabricated
    buckets."""
    by_hour: dict[int, list[Decimal]] = defaultdict(list)
    by_weekday: dict[int, list[Decimal]] = defaultdict(list)
    by_content_type: dict[str, list[Decimal]] = defaultdict(list)

    for s in samples:
        by_hour[s.local_hour].append(s.engagement)
        by_weekday[s.local_weekday].append(s.engagement)
        by_content_type[s.content_type].append(s.engagement)

    hour_stats = sorted(_bucket_stats(by_hour), key=lambda b: b.key)
    weekday_stats = sorted(_bucket_stats(by_weekday), key=lambda b: b.key)
    content_type_stats = sorted(_bucket_stats(by_content_type), key=lambda b: str(b.key))

    return PerformanceBreakdown(
        total_samples=len(samples),
        by_hour=tuple(hour_stats),
        by_weekday=tuple(weekday_stats),
        by_content_type=tuple(content_type_stats),
    )
