"""
Pure Phase 6 scoring engine (0-100). Zero dependency — same rationale as
`eligibility.py`: directly unit-testable with plain `python`, no
sqlalchemy/`app.models` import.

Scoring uses ONLY data already present on the product/its related rows —
no invented signal, no external call, no AI, nothing guessed. Five
components, weighted to sum to exactly 100 at the maximum:

| Component                                    | Points | Why |
|---|---|---|
| Selected caption passed validation           | 40 | Without this the product literally cannot publish (see `eligibility.py`) — the single heaviest-weighted signal. |
| At least one valid (downloaded) media item   | 30 | Same category of hard blocker — heavily weighted for the same reason. |
| `products.price` is known (not `None`)       | 10 | A listing with a known price is more complete than one where extraction failed or the source never stated it — a fact already recorded on the row, not invented. |
| Description looks substantial                | 10 | A longer stored description usually means a more informative source listing. Measured on the raw stored `description` only — nothing added. |
| Freshness of the source message              | 10 | Newer listings are more likely to still be in stock. A pure function of an existing timestamp (`source_message_date`), nothing else. |

The two "hard blocker" components alone are 70 of the 100 points on
purpose: a product that isn't even eligible to publish should never
outrank one that is, no matter how complete its other data looks. A
product missing either one scores at most 60 — and in practice never
gets queued at all regardless of score, since `eligibility.py` blocks it
outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CAPTION_POINTS = 40
MEDIA_POINTS = 30
PRICE_POINTS = 10
DESCRIPTION_POINTS = 10
FRESHNESS_POINTS = 10

MAX_SCORE = CAPTION_POINTS + MEDIA_POINTS + PRICE_POINTS + DESCRIPTION_POINTS + FRESHNESS_POINTS
assert MAX_SCORE == 100

# A description shorter than this (after stripping whitespace) is treated
# as "not substantial" — arbitrary but documented and easy to tune later;
# not a claim about the product, just a length heuristic on stored data.
DESCRIPTION_SUBSTANTIAL_MIN_LENGTH = 20

# (max_age_days_inclusive, points) checked in order — the first bucket
# whose threshold the source message's age falls within wins. Older than
# every bucket's threshold scores 0 freshness points.
_FRESHNESS_BUCKETS: list[tuple[float, int]] = [
    (1, FRESHNESS_POINTS),
    (3, 7),
    (7, 4),
    (30, 1),
]


@dataclass(frozen=True)
class ScoreBreakdown:
    caption: int
    media: int
    price: int
    description: int
    freshness: int

    @property
    def total(self) -> int:
        return self.caption + self.media + self.price + self.description + self.freshness


def _freshness_score(*, source_message_date: datetime, now: datetime) -> int:
    age_days = (now - source_message_date).total_seconds() / 86400
    for max_age_days, points in _FRESHNESS_BUCKETS:
        if age_days <= max_age_days:
            return points
    return 0


def compute_score_breakdown(
    *,
    has_selected_passed_caption: bool,
    has_valid_media: bool,
    price_present: bool,
    description: str | None,
    source_message_date: datetime,
    now: datetime,
) -> ScoreBreakdown:
    """Pure score computation. `now` is always passed in explicitly (this
    function never reads the system clock) so it stays deterministic and
    trivially testable. Both datetimes must be timezone-aware and in the
    same awareness (the project stores all `DateTime` columns as
    `timezone=True` — see `app.models.mixins`/model files — so this holds
    for every real caller)."""
    description_substantial = bool(
        description and len(description.strip()) >= DESCRIPTION_SUBSTANTIAL_MIN_LENGTH
    )

    return ScoreBreakdown(
        caption=CAPTION_POINTS if has_selected_passed_caption else 0,
        media=MEDIA_POINTS if has_valid_media else 0,
        price=PRICE_POINTS if price_present else 0,
        description=DESCRIPTION_POINTS if description_substantial else 0,
        freshness=_freshness_score(source_message_date=source_message_date, now=now),
    )


def compute_score(
    *,
    has_selected_passed_caption: bool,
    has_valid_media: bool,
    price_present: bool,
    description: str | None,
    source_message_date: datetime,
    now: datetime,
) -> int:
    """Convenience wrapper returning just the total (0-100)."""
    return compute_score_breakdown(
        has_selected_passed_caption=has_selected_passed_caption,
        has_valid_media=has_valid_media,
        price_present=price_present,
        description=description,
        source_message_date=source_message_date,
        now=now,
    ).total
