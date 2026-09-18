"""
Tests for `app.analytics.metrics.effective_engagement`.

Pure logic, zero dependency — run with plain `python`/`pytest`, no
database, no sqlalchemy import.
"""

from decimal import Decimal

from app.analytics.metrics import effective_engagement


def test_uses_stored_engagement_rate_directly_when_present():
    result = effective_engagement(
        engagement_rate=Decimal("0.0532"),
        likes=999,  # deliberately inconsistent - must be ignored, rate wins
        comments=999,
        shares=999,
        saves=999,
        reach=1,
        impressions=1,
    )
    assert result == Decimal("0.0532")


def test_derives_from_reach_when_rate_missing():
    result = effective_engagement(
        engagement_rate=None,
        likes=10,
        comments=2,
        shares=1,
        saves=3,
        reach=200,
        impressions=None,
    )
    assert result == Decimal(16) / Decimal(200)


def test_prefers_reach_over_impressions_when_both_present():
    result = effective_engagement(
        engagement_rate=None,
        likes=10,
        comments=0,
        shares=0,
        saves=0,
        reach=100,
        impressions=500,
    )
    assert result == Decimal(10) / Decimal(100)


def test_falls_back_to_impressions_when_reach_missing():
    result = effective_engagement(
        engagement_rate=None,
        likes=10,
        comments=0,
        shares=0,
        saves=0,
        reach=None,
        impressions=500,
    )
    assert result == Decimal(10) / Decimal(500)


def test_none_when_no_denominator_available():
    result = effective_engagement(
        engagement_rate=None,
        likes=10,
        comments=2,
        shares=1,
        saves=3,
        reach=None,
        impressions=None,
    )
    assert result is None


def test_none_when_denominator_present_but_zero():
    result = effective_engagement(
        engagement_rate=None,
        likes=10,
        comments=2,
        shares=1,
        saves=3,
        reach=0,
        impressions=None,
    )
    assert result is None


def test_none_when_no_engagement_action_numbers_at_all():
    result = effective_engagement(
        engagement_rate=None,
        likes=None,
        comments=None,
        shares=None,
        saves=None,
        reach=500,
        impressions=None,
    )
    assert result is None


def test_treats_partial_action_numbers_as_zero_for_the_missing_ones():
    # Only likes recorded; comments/shares/saves genuinely unknown but at
    # least one real action number exists, so a rate IS derivable.
    result = effective_engagement(
        engagement_rate=None,
        likes=5,
        comments=None,
        shares=None,
        saves=None,
        reach=100,
        impressions=None,
    )
    assert result == Decimal(5) / Decimal(100)


def test_never_returns_zero_for_missing_data_it_returns_none():
    result = effective_engagement(
        engagement_rate=None,
        likes=None,
        comments=None,
        shares=None,
        saves=None,
        reach=None,
        impressions=None,
    )
    assert result is None
    assert result != Decimal(0)


def test_zero_engagement_rate_is_still_a_real_value_not_treated_as_missing():
    # A real, stored 0.0 rate must be used as-is, not confused with "no
    # data" - this is different from the None cases above.
    result = effective_engagement(
        engagement_rate=Decimal("0.0"),
        likes=None,
        comments=None,
        shares=None,
        saves=None,
        reach=None,
        impressions=None,
    )
    assert result == Decimal("0.0")
    assert result is not None
