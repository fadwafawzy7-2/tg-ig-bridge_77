"""
Pure engagement-metric computation from real, already-stored analytics
numbers. Zero dependency (no `app.models` import — same rationale as
`app.queue.scoring`/`app.queue.eligibility`: directly unit-testable with
plain `python`, no sqlalchemy).

NEVER invents a number. If the stored `engagement_rate` is present, it is
used as-is (already computed by whatever captured it). If it is absent, a
rate is derived ONLY from other already-stored real numbers
(likes+comments+shares+saves relative to reach, or impressions if reach
is unavailable) — never from an assumption, a default, or a guess. If
neither the direct rate nor enough raw numbers exist to derive one, this
returns `None`: "we don't know yet" is the honest answer, and callers
must treat `None` as "exclude this sample from analysis", never as zero
(zero would falsely say "this performed terribly" about something we
simply have no data for).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def effective_engagement(
    *,
    engagement_rate: Decimal | None,
    likes: int | None,
    comments: int | None,
    shares: int | None,
    saves: int | None,
    reach: int | None,
    impressions: int | None,
) -> Decimal | None:
    """The engagement rate to use for analysis, or `None` if it can't be
    determined from real stored data."""
    if engagement_rate is not None:
        return engagement_rate

    denominator = reach if reach else impressions
    if not denominator or denominator <= 0:
        return None

    action_fields = (likes, comments, shares, saves)
    if all(v is None for v in action_fields):
        # No engagement-action numbers stored at all - nothing to derive
        # a rate from, even though we do have a denominator.
        return None

    total_actions = sum(v for v in action_fields if v is not None)
    try:
        return Decimal(total_actions) / Decimal(denominator)
    except InvalidOperation:
        return None
