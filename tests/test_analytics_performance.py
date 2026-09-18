"""
Tests for `app.analytics.performance.compute_breakdown`.

Pure logic, zero dependency — run with plain `python`/`pytest`.
"""

from decimal import Decimal

from app.analytics.performance import EngagementSample, compute_breakdown


def _sample(hour, weekday, content_type, engagement):
    return EngagementSample(
        local_hour=hour, local_weekday=weekday, content_type=content_type, engagement=Decimal(str(engagement))
    )


def test_empty_samples_produce_honest_empty_breakdown():
    breakdown = compute_breakdown([])
    assert breakdown.total_samples == 0
    assert breakdown.by_hour == ()
    assert breakdown.by_weekday == ()
    assert breakdown.by_content_type == ()


def test_single_sample_appears_in_all_three_breakdowns():
    breakdown = compute_breakdown([_sample(10, 2, "POST", "0.05")])
    assert breakdown.total_samples == 1
    assert len(breakdown.by_hour) == 1
    assert breakdown.by_hour[0].key == 10
    assert breakdown.by_hour[0].sample_count == 1
    assert breakdown.by_hour[0].mean_engagement == Decimal("0.05")
    assert breakdown.by_weekday[0].key == 2
    assert breakdown.by_content_type[0].key == "POST"


def test_groups_and_averages_correctly_by_hour():
    samples = [_sample(10, 0, "POST", "0.10"), _sample(10, 1, "POST", "0.20"), _sample(18, 0, "POST", "0.30")]
    breakdown = compute_breakdown(samples)
    hour_10 = next(b for b in breakdown.by_hour if b.key == 10)
    hour_18 = next(b for b in breakdown.by_hour if b.key == 18)
    assert hour_10.sample_count == 2
    assert hour_10.mean_engagement == Decimal("0.15")
    assert hour_18.sample_count == 1
    assert hour_18.mean_engagement == Decimal("0.30")


def test_groups_and_averages_correctly_by_content_type():
    samples = [
        _sample(10, 0, "POST", "0.10"),
        _sample(11, 0, "POST", "0.30"),
        _sample(12, 0, "REEL", "0.50"),
        _sample(13, 0, "STORY", "0.05"),
    ]
    breakdown = compute_breakdown(samples)
    by_type = {b.key: b for b in breakdown.by_content_type}
    assert by_type["POST"].sample_count == 2
    assert by_type["POST"].mean_engagement == Decimal("0.20")
    assert by_type["REEL"].sample_count == 1
    assert by_type["STORY"].sample_count == 1


def test_small_bucket_is_still_reported_never_hidden():
    # A single sample must still show up (with sample_count=1) - unlike
    # the best_time_engine, plain reporting never gates on sample size.
    breakdown = compute_breakdown([_sample(3, 5, "POST", "0.01")])
    assert breakdown.by_hour[0].sample_count == 1


def test_buckets_are_sorted_by_key():
    samples = [_sample(20, 0, "POST", "0.1"), _sample(5, 0, "POST", "0.1"), _sample(12, 0, "POST", "0.1")]
    breakdown = compute_breakdown(samples)
    assert [b.key for b in breakdown.by_hour] == [5, 12, 20]


def test_weekday_full_week_range():
    samples = [_sample(10, wd, "POST", "0.1") for wd in range(7)]
    breakdown = compute_breakdown(samples)
    assert [b.key for b in breakdown.by_weekday] == [0, 1, 2, 3, 4, 5, 6]
