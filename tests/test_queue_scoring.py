"""
Tests for `app.queue.scoring`.

Pure logic, zero dependency (like `app.ai.validator` /
`app.parsing.text_parser`) — run with plain `python`/`pytest`, no
database, no sqlalchemy import at all.
"""

from datetime import datetime, timedelta, timezone

from app.queue.scoring import (
    CAPTION_POINTS,
    DESCRIPTION_POINTS,
    DESCRIPTION_SUBSTANTIAL_MIN_LENGTH,
    FRESHNESS_POINTS,
    MAX_SCORE,
    MEDIA_POINTS,
    PRICE_POINTS,
    compute_score,
    compute_score_breakdown,
)

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def test_max_score_is_100():
    assert MAX_SCORE == 100
    assert CAPTION_POINTS + MEDIA_POINTS + PRICE_POINTS + DESCRIPTION_POINTS + FRESHNESS_POINTS == 100


def test_fully_complete_fresh_product_scores_100():
    breakdown = compute_score_breakdown(
        has_selected_passed_caption=True,
        has_valid_media=True,
        price_present=True,
        description="A" * DESCRIPTION_SUBSTANTIAL_MIN_LENGTH,
        source_message_date=NOW,
        now=NOW,
    )
    assert breakdown.total == 100
    assert compute_score(
        has_selected_passed_caption=True,
        has_valid_media=True,
        price_present=True,
        description="A" * DESCRIPTION_SUBSTANTIAL_MIN_LENGTH,
        source_message_date=NOW,
        now=NOW,
    ) == 100


def test_completely_empty_product_scores_0():
    breakdown = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    assert breakdown.total == 0


def test_caption_component_is_all_or_nothing():
    with_caption = compute_score_breakdown(
        has_selected_passed_caption=True,
        has_valid_media=False,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    without_caption = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    assert with_caption.caption == CAPTION_POINTS
    assert without_caption.caption == 0
    assert with_caption.total - without_caption.total == CAPTION_POINTS


def test_media_component_is_all_or_nothing():
    with_media = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=True,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    assert with_media.media == MEDIA_POINTS


def test_caption_and_media_together_dominate_the_score():
    hard_blockers_only = compute_score_breakdown(
        has_selected_passed_caption=True,
        has_valid_media=True,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    everything_else_only = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=True,
        description="A" * 50,
        source_message_date=NOW,
        now=NOW,
    )
    assert hard_blockers_only.total > everything_else_only.total
    assert hard_blockers_only.total == CAPTION_POINTS + MEDIA_POINTS  # == 70


def test_price_present_adds_price_points():
    breakdown = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=True,
        description=None,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    assert breakdown.price == PRICE_POINTS


def test_short_description_scores_zero_description_points():
    breakdown = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description="short",
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    assert breakdown.description == 0


def test_substantial_description_scores_full_description_points():
    breakdown = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description="x" * DESCRIPTION_SUBSTANTIAL_MIN_LENGTH,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    assert breakdown.description == DESCRIPTION_POINTS


def test_description_length_boundary_is_inclusive():
    exactly_at_threshold = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description="x" * DESCRIPTION_SUBSTANTIAL_MIN_LENGTH,
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    one_short = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description="x" * (DESCRIPTION_SUBSTANTIAL_MIN_LENGTH - 1),
        source_message_date=NOW - timedelta(days=90),
        now=NOW,
    )
    assert exactly_at_threshold.description == DESCRIPTION_POINTS
    assert one_short.description == 0


def test_freshness_decreases_with_age():
    fresh = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(hours=2),
        now=NOW,
    )
    week_old = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(days=5),
        now=NOW,
    )
    ancient = compute_score_breakdown(
        has_selected_passed_caption=False,
        has_valid_media=False,
        price_present=False,
        description=None,
        source_message_date=NOW - timedelta(days=200),
        now=NOW,
    )
    assert fresh.freshness == FRESHNESS_POINTS
    assert 0 < week_old.freshness < fresh.freshness
    assert ancient.freshness == 0


def test_score_is_deterministic_for_the_same_explicit_now():
    kwargs = dict(
        has_selected_passed_caption=True,
        has_valid_media=True,
        price_present=True,
        description="A" * 30,
        source_message_date=NOW - timedelta(days=2),
        now=NOW,
    )
    assert compute_score(**kwargs) == compute_score(**kwargs)
    assert compute_score_breakdown(**kwargs) == compute_score_breakdown(**kwargs)


def test_score_is_bounded_0_to_100_across_many_combinations():
    for caption in (True, False):
        for media in (True, False):
            for price in (True, False):
                for desc in (None, "short", "x" * 40):
                    for age_days in (0, 2, 5, 10, 40, 400):
                        score = compute_score(
                            has_selected_passed_caption=caption,
                            has_valid_media=media,
                            price_present=price,
                            description=desc,
                            source_message_date=NOW - timedelta(days=age_days),
                            now=NOW,
                        )
                        assert 0 <= score <= 100
