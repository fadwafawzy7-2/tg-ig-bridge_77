"""
Tests for `app.analytics.best_time_engine.compute_best_times`.

Pure logic, zero dependency — run with plain `python`/`pytest`. Covers
insufficient-data fallback, ranking, and "learning" as more data
accumulates.
"""

from decimal import Decimal

from app.analytics.best_time_engine import (
    MIN_SAMPLES_PER_HOUR,
    MIN_TOTAL_SAMPLES,
    compute_best_times,
)
from app.analytics.performance import EngagementSample

FALLBACK = (10, 22)


def _sample(hour, weekday, engagement, content_type="POST"):
    return EngagementSample(
        local_hour=hour, local_weekday=weekday, content_type=content_type, engagement=Decimal(str(engagement))
    )


# --- Insufficient-data fallback -----------------------------------------


def test_zero_samples_falls_back_and_is_not_learned():
    result = compute_best_times([], fallback_window=FALLBACK)
    assert result.is_learned is False
    assert result.sample_count == 0
    assert result.ranked_hours == ()
    assert result.fallback_window == FALLBACK
    assert "fallback" in result.notes.lower() or "default" in result.notes.lower()


def test_fewer_than_min_total_samples_falls_back():
    samples = [_sample(10, 0, "0.1") for _ in range(MIN_TOTAL_SAMPLES - 1)]
    result = compute_best_times(samples, fallback_window=FALLBACK)
    assert result.is_learned is False
    assert result.sample_count == MIN_TOTAL_SAMPLES - 1


def test_fallback_never_claims_a_proven_best_time():
    result = compute_best_times([], fallback_window=FALLBACK)
    assert result.best_hour is None
    lowered = result.notes.lower()
    assert "not" in lowered or "instead" in lowered  # explicitly disclaims a proven claim


def test_enough_total_but_spread_too_thin_per_hour_falls_back():
    # MIN_TOTAL_SAMPLES total, but each in its own distinct hour so no
    # single hour reaches MIN_SAMPLES_PER_HOUR.
    samples = [_sample(h, 0, "0.1") for h in range(MIN_TOTAL_SAMPLES)]
    result = compute_best_times(samples, fallback_window=FALLBACK, min_samples_per_hour=2)
    assert result.is_learned is False
    assert result.sample_count == MIN_TOTAL_SAMPLES  # data was seen, just not concentrated enough


# --- Ranking --------------------------------------------------------


def test_ranks_hours_by_mean_engagement_descending():
    samples = (
        [_sample(10, 0, "0.30") for _ in range(3)]
        + [_sample(18, 1, "0.10") for _ in range(3)]
        + [_sample(14, 2, "0.20") for _ in range(3)]
    )
    result = compute_best_times(samples, fallback_window=FALLBACK)
    assert result.is_learned is True
    assert [hr.hour for hr in result.ranked_hours] == [10, 14, 18]
    assert result.best_hour == 10


def test_ranks_weekdays_by_mean_engagement_descending():
    samples = (
        [_sample(10, 0, "0.10") for _ in range(3)]
        + [_sample(10, 6, "0.50") for _ in range(3)]
    )
    result = compute_best_times(samples, fallback_window=FALLBACK)
    assert result.is_learned is True
    assert result.ranked_weekdays[0].weekday == 6
    assert result.ranked_weekdays[0].mean_engagement == Decimal("0.50")


def test_hour_below_min_samples_is_excluded_from_ranking_even_if_high_engagement():
    samples = (
        [_sample(10, 0, "0.99")]  # only 1 sample - below MIN_SAMPLES_PER_HOUR
        + [_sample(18, 0, "0.10") for _ in range(3)]
        + [_sample(20, 0, "0.05") for _ in range(3)]
    )
    result = compute_best_times(samples, fallback_window=FALLBACK)
    ranked_hours_set = {hr.hour for hr in result.ranked_hours}
    assert 10 not in ranked_hours_set  # excluded despite the highest raw engagement
    assert result.best_hour == 18


def test_sample_counts_are_reported_per_hour():
    samples = [_sample(10, 0, "0.1") for _ in range(4)] + [_sample(18, 0, "0.2") for _ in range(3)]
    result = compute_best_times(samples, fallback_window=FALLBACK)
    counts = {hr.hour: hr.sample_count for hr in result.ranked_hours}
    assert counts[10] == 4
    assert counts[18] == 3


# --- "Learning" as more data accumulates ---------------------------------


def test_recommendation_updates_as_more_data_accumulates():
    # Stage 1: not enough data yet.
    stage1 = [_sample(10, 0, "0.1") for _ in range(2)]
    result1 = compute_best_times(stage1, fallback_window=FALLBACK)
    assert result1.is_learned is False

    # Stage 2: enough data now, hour 10 leads.
    stage2 = stage1 + [_sample(10, 0, "0.1")] + [_sample(18, 0, "0.05") for _ in range(3)]
    result2 = compute_best_times(stage2, fallback_window=FALLBACK)
    assert result2.is_learned is True
    assert result2.best_hour == 10

    # Stage 3: more real data shifts the leader to hour 18 - the engine
    # must re-rank based on the CURRENT full dataset, not stick to an
    # earlier conclusion (no persisted/stale state).
    stage3 = stage2 + [_sample(18, 0, "0.90") for _ in range(3)]
    result3 = compute_best_times(stage3, fallback_window=FALLBACK)
    assert result3.is_learned is True
    assert result3.best_hour == 18
    assert result3.sample_count == len(stage3)


def test_is_a_pure_function_same_input_same_output():
    samples = [_sample(10, 0, "0.1") for _ in range(3)] + [_sample(18, 0, "0.2") for _ in range(3)]
    result_a = compute_best_times(samples, fallback_window=FALLBACK)
    result_b = compute_best_times(samples, fallback_window=FALLBACK)
    assert result_a == result_b


# --- Sanity on the constants themselves --------------------------------


def test_default_thresholds_are_positive():
    assert MIN_SAMPLES_PER_HOUR > 0
    assert MIN_TOTAL_SAMPLES > 0
