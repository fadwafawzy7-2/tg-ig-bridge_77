"""
DB-facing layer for Phase 7: loads REAL `published_posts` / `stories`
rows (only ones that actually have a `published_at`, i.e. actually went
out) joined with their latest `analytics` snapshot, converts that
timestamp to Asia/Gaza local time, derives a usable engagement number via
`metrics.effective_engagement` (or excludes the row if none can be
derived — never invents one), and feeds the result into the pure
`performance.py` / `best_time_engine.py` modules.

Read-only with respect to Phase 6: imports
`app.queue.scheduler.SCHEDULING_WINDOW_START_HOUR` /
`_END_HOUR` for the Best-Time Engine's fallback window, and
`app.core.config.get_settings().SCHEDULER_TIMEZONE` for the timezone —
both read, never modified. Nothing in this module writes to
`products`/`scheduled_posts`/`daily_limits`, and nothing here changes
queue ordering or scoring.

"Latest snapshot per post/story": `analytics` can hold multiple
`metric_date` rows per parent (engagement grows over time — see that
model's docstring), so using every row would double-count the same post
multiple times and bias the result toward posts that have simply been
tracked longer. Only the most recent snapshot per parent is used —
one usable engagement sample per published post/story, which is the
correct question for "when should we post" (a "how has it changed over
time" report reads all snapshots directly instead — out of scope here).
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.best_time_engine import BestTimeResult, compute_best_times
from app.analytics.metrics import effective_engagement
from app.analytics.performance import EngagementSample, PerformanceBreakdown, compute_breakdown
from app.core.config import get_settings
from app.models.analytics import Analytics
from app.models.enums import ContentType
from app.models.published_post import PublishedPost
from app.models.story import Story
from app.queue.scheduler import SCHEDULING_WINDOW_END_HOUR, SCHEDULING_WINDOW_START_HOUR


async def _load_post_samples(
    session: AsyncSession,
    *,
    tz: ZoneInfo,
    content_type: ContentType | None,
    since: date_ | None,
    until: date_ | None,
) -> list[EngagementSample]:
    latest_dates = (
        select(
            Analytics.published_post_id.label("published_post_id"),
            func.max(Analytics.metric_date).label("max_date"),
        )
        .where(Analytics.published_post_id.isnot(None))
        .group_by(Analytics.published_post_id)
        .subquery()
    )

    stmt = (
        select(Analytics, PublishedPost)
        .join(PublishedPost, Analytics.published_post_id == PublishedPost.id)
        .join(
            latest_dates,
            (Analytics.published_post_id == latest_dates.c.published_post_id)
            & (Analytics.metric_date == latest_dates.c.max_date),
        )
    )
    if content_type is not None:
        stmt = stmt.where(PublishedPost.content_type == content_type)
    if since is not None:
        stmt = stmt.where(PublishedPost.published_at >= _start_of(since, tz))
    if until is not None:
        stmt = stmt.where(PublishedPost.published_at < _start_of(until, tz, plus_one_day=True))

    rows = (await session.execute(stmt)).all()

    samples: list[EngagementSample] = []
    for analytics_row, post in rows:
        engagement = effective_engagement(
            engagement_rate=analytics_row.engagement_rate,
            likes=analytics_row.likes,
            comments=analytics_row.comments,
            shares=analytics_row.shares,
            saves=analytics_row.saves,
            reach=analytics_row.reach,
            impressions=analytics_row.impressions,
        )
        if engagement is None:
            continue
        local_dt = post.published_at.astimezone(tz)
        samples.append(
            EngagementSample(
                local_hour=local_dt.hour,
                local_weekday=local_dt.weekday(),
                content_type=post.content_type.value,
                engagement=engagement,
            )
        )
    return samples


async def _load_story_samples(
    session: AsyncSession,
    *,
    tz: ZoneInfo,
    content_type: ContentType | None,
    since: date_ | None,
    until: date_ | None,
) -> list[EngagementSample]:
    if content_type is not None and content_type != ContentType.STORY:
        return []

    latest_dates = (
        select(
            Analytics.story_id.label("story_id"),
            func.max(Analytics.metric_date).label("max_date"),
        )
        .where(Analytics.story_id.isnot(None))
        .group_by(Analytics.story_id)
        .subquery()
    )

    stmt = (
        select(Analytics, Story)
        .join(Story, Analytics.story_id == Story.id)
        .join(
            latest_dates,
            (Analytics.story_id == latest_dates.c.story_id)
            & (Analytics.metric_date == latest_dates.c.max_date),
        )
        .where(Story.published_at.isnot(None))
    )
    if since is not None:
        stmt = stmt.where(Story.published_at >= _start_of(since, tz))
    if until is not None:
        stmt = stmt.where(Story.published_at < _start_of(until, tz, plus_one_day=True))

    rows = (await session.execute(stmt)).all()

    samples: list[EngagementSample] = []
    for analytics_row, story in rows:
        engagement = effective_engagement(
            engagement_rate=analytics_row.engagement_rate,
            likes=analytics_row.likes,
            comments=analytics_row.comments,
            shares=analytics_row.shares,
            saves=analytics_row.saves,
            reach=analytics_row.reach,
            impressions=analytics_row.impressions,
        )
        if engagement is None:
            continue
        local_dt = story.published_at.astimezone(tz)
        samples.append(
            EngagementSample(
                local_hour=local_dt.hour,
                local_weekday=local_dt.weekday(),
                content_type=ContentType.STORY.value,
                engagement=engagement,
            )
        )
    return samples


def _start_of(day: date_, tz: ZoneInfo, *, plus_one_day: bool = False) -> datetime:
    dt = datetime(day.year, day.month, day.day, tzinfo=tz)
    return dt + timedelta(days=1) if plus_one_day else dt


async def load_engagement_samples(
    session: AsyncSession,
    *,
    content_type: ContentType | None = None,
    since: date_ | None = None,
    until: date_ | None = None,
) -> list[EngagementSample]:
    """Real, usable engagement samples (one per published post/story,
    latest analytics snapshot only) — the shared input both
    `get_performance_breakdown` and `get_best_times` build on."""
    settings = get_settings()
    tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)

    post_samples = await _load_post_samples(
        session, tz=tz, content_type=content_type, since=since, until=until
    )
    story_samples = await _load_story_samples(
        session, tz=tz, content_type=content_type, since=since, until=until
    )
    return post_samples + story_samples


async def get_performance_breakdown(
    session: AsyncSession,
    *,
    content_type: ContentType | None = None,
    since: date_ | None = None,
    until: date_ | None = None,
) -> PerformanceBreakdown:
    """Descriptive performance analysis by hour / weekday / content type,
    from real data only. See `performance.py` — every bucket carries its
    own sample_count, never hidden or gated."""
    samples = await load_engagement_samples(
        session, content_type=content_type, since=since, until=until
    )
    return compute_breakdown(samples)


async def get_best_times(
    session: AsyncSession,
    *,
    content_type: ContentType | None = None,
    since: date_ | None = None,
    until: date_ | None = None,
) -> BestTimeResult:
    """The Best-Time Engine result for Asia/Gaza: a learned recommendation
    if there's enough real data, otherwise an honestly-labeled fallback to
    the existing default scheduling window (from `app.queue.scheduler`,
    read-only) — never a claim of a proven best time without the data to
    back it."""
    samples = await load_engagement_samples(
        session, content_type=content_type, since=since, until=until
    )
    fallback_window = (SCHEDULING_WINDOW_START_HOUR, SCHEDULING_WINDOW_END_HOUR)
    return compute_best_times(samples, fallback_window=fallback_window)
