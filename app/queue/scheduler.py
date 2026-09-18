"""
QUEUED -> SCHEDULED.

For each QUEUED product (highest score first), assigns a concrete
`scheduled_posts` row IF there is remaining capacity today under
`daily_limits` for the target content_type. `daily_limits.max_allowed` is
strictly a ceiling ("MAXIMUM وليس minimum" — see `daily_limit.py`'s
docstring): if the limit is 6 and only 4 products are QUEUED, exactly 4
get scheduled (never invented to fill the other 2); if the limit is 0,
nothing gets scheduled at all; if there is no `DailyLimit` row configured
for today's (date, content_type) at all, this is treated the same as a
limit of 0 — scheduling is conservative-by-default, never assuming an
unconfigured day means "unlimited".

Capacity already consumed for a given (date, content_type) is measured as
the count of ACTIVE `scheduled_posts` rows (PENDING/SCHEDULED/PROCESSING
— the same status set the Phase 2 partial unique index uses) whose
`scheduled_for` falls on that date in the scheduler's timezone, PLUS
`daily_limits.published_count` (incremented by a later phase once a post
is actually confirmed published — always 0 for now, since publishing
itself is out of scope here). Both terms matter together — see
`remaining_capacity()`'s docstring for why counting only one of them
would be wrong.

Idempotency (restart-safety) is DB-enforced, not memory-only, two ways
(both already existed from the Phase 2 migration — nothing added here):
1. `scheduled_posts.idempotency_key` is globally UNIQUE. This module
   derives it deterministically from (product_id, content_type, date), so
   re-running the scheduler for a date/product it already handled hits a
   unique-constraint conflict, which is caught and treated as "already
   scheduled" rather than creating a duplicate.
2. `uq_scheduled_posts_active_product_content_type` (a partial unique
   index) additionally guarantees at most one ACTIVE scheduled post per
   (product_id, content_type) regardless of date, independent of the
   idempotency key.

Timezone-aware throughout: "today", the day-boundary used to count
already-consumed capacity, and the scheduled_for time-of-day assigned to
each slot are all computed in `settings.SCHEDULER_TIMEZONE` (`Asia/Gaza`
by default) via `app.queue.time_utils` (stdlib `zoneinfo` — no extra
dependency), never the server's local time or naive UTC.

Slot-time assignment is intentionally simple — evenly spread across a
fixed daily posting window (`SCHEDULING_WINDOW_START_HOUR` to
`SCHEDULING_WINDOW_END_HOUR`, both local to the scheduler timezone) in
score order. This is NOT a learned "best time to post" model — Best-Time
Learning is explicitly out of scope for this phase (see
`app/queue/__init__.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.caption import Caption
from app.models.daily_limit import DailyLimit
from app.models.enums import ContentType, PostPublishStatus, ProductStatus, MediaType, StoryStatus
from app.models.product import Product
from app.models.story import Story
from app.models.media import Media
from app.models.scheduled_post import ScheduledPost
from app.queue.time_utils import start_of_day_in_timezone, today_in_timezone

logger = logging.getLogger(__name__)

ACTIVE_SCHEDULED_POST_STATUSES = (
    PostPublishStatus.PENDING,
    PostPublishStatus.SCHEDULED,
    PostPublishStatus.PROCESSING,
)

# Fixed daily posting window (local to the scheduler timezone) slots are
# spread across. Simple/deterministic on purpose — see module docstring
# on Best-Time Learning being out of scope for this phase.
SCHEDULING_WINDOW_START_HOUR = 10
SCHEDULING_WINDOW_END_HOUR = 22


def build_idempotency_key(
    *, product_id: int, content_type: ContentType, target_date: date_
) -> str:
    return f"product:{product_id}:content:{content_type.value}:date:{target_date.isoformat()}"


@dataclass(frozen=True)
class ScheduleResult:
    product_id: int
    # "scheduled" | "limit_reached" | "already_scheduled" | "no_daily_limit_configured"
    outcome: str
    scheduled_post_id: int | None = None
    scheduled_for: datetime | None = None


async def remaining_capacity(
    session: AsyncSession, *, target_date: date_, content_type: ContentType, tz_name: str
) -> int | None:
    """Additional posts that may still be scheduled for `target_date` /
    `content_type`, or `None` if there's no configured limit at all
    (callers treat `None` as zero capacity — see module docstring).

    Consumed capacity = already-published today (`daily_limits.published_count`,
    maintained by a later publish-execution phase — always 0 in this
    phase, since nothing sets it yet) PLUS currently-active scheduled
    posts for today (PENDING/SCHEDULED/PROCESSING — what THIS phase
    creates). Both terms are needed together, not just one: counting only
    `published_count` would let this phase over-schedule without limit
    (nothing increments it yet); counting only the active-rows term would
    under-count once publishing starts elsewhere, since a row leaves the
    active set the moment it's published — silently "returning" capacity
    that was already spent. Summing both keeps this correct today AND
    once a later phase starts publishing, with no further change needed
    here.
    """
    limit_row = (
        await session.execute(
            select(DailyLimit).where(
                DailyLimit.date == target_date, DailyLimit.content_type == content_type
            )
        )
    ).scalars().first()

    if limit_row is None:
        return None

    day_start = start_of_day_in_timezone(target_date, tz_name)
    day_end = day_start + timedelta(days=1)

    if content_type == ContentType.STORY:
        active_count = (
            await session.execute(
                select(func.count())
                .select_from(Story)
                .where(
                    Story.status.in_((StoryStatus.PENDING, StoryStatus.SCHEDULED, StoryStatus.PROCESSING)),
                    Story.scheduled_for >= day_start,
                    Story.scheduled_for < day_end,
                )
            )
        ).scalar_one()
    else:
        active_count = (
            await session.execute(
                select(func.count())
                .select_from(ScheduledPost)
                .where(
                    ScheduledPost.content_type == content_type,
                    ScheduledPost.status.in_(ACTIVE_SCHEDULED_POST_STATUSES),
                    ScheduledPost.scheduled_for >= day_start,
                    ScheduledPost.scheduled_for < day_end,
                )
            )
        ).scalar_one()

    consumed = active_count + limit_row.published_count
    return max(0, limit_row.max_allowed - consumed)


def _slot_time(*, target_date: date_, tz_name: str, index: int, total: int) -> datetime:
    tz = ZoneInfo(tz_name)
    window_start = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        SCHEDULING_WINDOW_START_HOUR,
        tzinfo=tz,
    )
    window_end = datetime(
        target_date.year, target_date.month, target_date.day, SCHEDULING_WINDOW_END_HOUR, tzinfo=tz
    )
    if total <= 1:
        return window_start
    step = (window_end - window_start) / total
    return window_start + step * index


async def selected_caption_id(session: AsyncSession, product_id: int) -> int | None:
    result = await session.execute(
        select(Caption.id).where(Caption.product_id == product_id, Caption.is_selected.is_(True))
    )
    return result.scalars().first()



async def ensure_default_daily_limits(session: AsyncSession, target_date: date_) -> None:
    """Create conservative daily limits for a new day if absent.

    Defaults are 1 Post, 1 Reel and 10 Stories; Telegram dashboard changes
    remain authoritative for the current day and future runs.
    """
    from app.models.daily_limit import DailyLimit
    settings = get_settings()
    defaults = {
        ContentType.POST: settings.DEFAULT_DAILY_POST_LIMIT,
        ContentType.REEL: settings.DEFAULT_DAILY_REEL_LIMIT,
        ContentType.STORY: settings.DEFAULT_DAILY_STORY_LIMIT,
    }
    for content_type, maximum in defaults.items():
        row = (await session.execute(select(DailyLimit).where(DailyLimit.date == target_date, DailyLimit.content_type == content_type))).scalars().first()
        if row is None:
            session.add(DailyLimit(date=target_date, content_type=content_type, max_allowed=max(0, maximum), published_count=0))
    await session.commit()


async def _product_media_types(session: AsyncSession, product_id: int) -> set[MediaType]:
    rows = (await session.execute(select(Media.media_type).where(Media.product_id == product_id, Media.file_path.is_not(None)))).scalars().all()
    return set(rows)


@dataclass(frozen=True)
class StoryScheduleSummary:
    created: int


async def schedule_stories(session: AsyncSession, *, target_date: date_ | None = None) -> StoryScheduleSummary:
    """Create due Story rows conservatively, allowing repeats after cooldown."""
    settings = get_settings()
    resolved_date = target_date or today_in_timezone(settings.SCHEDULER_TIMEZONE)
    remaining = await remaining_capacity(session, target_date=resolved_date, content_type=ContentType.STORY, tz_name=settings.SCHEDULER_TIMEZONE)
    if remaining is None or remaining <= 0:
        return StoryScheduleSummary(0)
    cooldown = timedelta(hours=max(0, settings.STORY_COOLDOWN_HOURS))
    products = (await session.execute(
        select(Product).where(Product.status.in_((ProductStatus.VALIDATED, ProductStatus.ELIGIBLE, ProductStatus.QUEUED, ProductStatus.SCHEDULED, ProductStatus.PUBLISHED))).order_by(Product.score.desc().nullslast(), Product.id.asc()).limit(100)
    )).scalars().all()
    created = 0
    tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)
    for product in products:
        if created >= remaining:
            break
        media = (await session.execute(select(Media).where(Media.product_id == product.id, Media.file_path.is_not(None), Media.media_type.in_((MediaType.PHOTO, MediaType.VIDEO))).order_by(Media.display_order))).scalars().all()
        if not media:
            continue
        latest = (await session.execute(select(Story).where(Story.product_id == product.id, Story.status.in_((StoryStatus.PENDING, StoryStatus.SCHEDULED, StoryStatus.PROCESSING, StoryStatus.PUBLISHED))).order_by(Story.id.desc()).limit(1))).scalars().first()
        if latest is not None:
            if latest.status in (StoryStatus.PENDING, StoryStatus.SCHEDULED, StoryStatus.PROCESSING):
                continue
            if latest.published_at is not None and (datetime.now(tz) - latest.published_at.astimezone(tz)) < cooldown:
                continue
        media_item = media[0]
        key = f"story:{product.id}:{resolved_date.isoformat()}:{media_item.id}"
        exists = (await session.execute(select(Story).where(Story.idempotency_key == key))).scalars().first()
        if exists is not None:
            continue
        row = Story(product_id=product.id, media_id=media_item.id, status=StoryStatus.SCHEDULED, scheduled_for=datetime.now(tz), idempotency_key=key)
        session.add(row)
        try:
            await session.flush()
        except Exception:
            await session.rollback()
            continue
        await session.commit()
        created += 1
    return StoryScheduleSummary(created)

async def run_scheduler(
    session: AsyncSession,
    *,
    content_type: ContentType | None = None,
    target_date: date_ | None = None,
    limit: int | None = None,
) -> list[ScheduleResult]:
    """Schedule queued products using the first compatible POST/REEL capacity.

    Video-capable products prefer REEL; photo-only products use POST. A product
    is scheduled at most once for the day, preserving the original duplicate
    protection while making the Phase-10 daily Post/Reel limits actually usable.
    """
    settings = get_settings()
    tz_name = settings.SCHEDULER_TIMEZONE
    resolved_date = target_date or today_in_timezone(tz_name)
    products = (await session.execute(
        select(Product)
        .where(Product.status == ProductStatus.QUEUED)
        .where(Product.preferred_content_type != ContentType.STORY)
        .order_by(Product.score.desc().nullslast(), Product.id.asc())
    )).scalars().all()
    if limit is not None:
        products = products[:limit]
    results: list[ScheduleResult] = []
    remaining_by_type = {
        ContentType.POST: await remaining_capacity(session, target_date=resolved_date, content_type=ContentType.POST, tz_name=tz_name),
        ContentType.REEL: await remaining_capacity(session, target_date=resolved_date, content_type=ContentType.REEL, tz_name=tz_name),
    }
    slots_by_type = {k: (v if v is not None else 0) for k, v in remaining_by_type.items()}
    selected: list[tuple[Product, ContentType]] = []
    for product in products:
        if product.preferred_content_type in (ContentType.POST, ContentType.REEL):
            # An explicit choice made right after a manual capture always
            # wins — over both the batch-wide `content_type` override and
            # the automatic photo-vs-video guess below.
            candidates = [product.preferred_content_type]
        elif content_type is not None and content_type in (ContentType.POST, ContentType.REEL):
            candidates = [content_type]
        else:
            media_types = await _product_media_types(session, product.id)
            candidates = [ContentType.REEL, ContentType.POST] if MediaType.VIDEO in media_types else [ContentType.POST]
        chosen = next((ct for ct in candidates if slots_by_type[ct] > 0), None)
        if chosen is None:
            results.append(ScheduleResult(product_id=product.id, outcome="limit_reached"))
            continue
        slots_by_type[chosen] -= 1
        selected.append((product, chosen))
    totals = {ct: sum(1 for _, chosen in selected if chosen == ct) for ct in (ContentType.POST, ContentType.REEL)}
    indices = {ContentType.POST: 0, ContentType.REEL: 0}
    for product, chosen in selected:
        idempotency_key = build_idempotency_key(product_id=product.id, content_type=chosen, target_date=resolved_date)
        scheduled_for = _slot_time(target_date=resolved_date, tz_name=tz_name, index=indices[chosen], total=max(1, totals[chosen]))
        indices[chosen] += 1
        caption_id = await selected_caption_id(session, product.id)
        scheduled_post = ScheduledPost(product_id=product.id, content_type=chosen, caption_id=caption_id, status=PostPublishStatus.SCHEDULED, scheduled_for=scheduled_for, idempotency_key=idempotency_key)
        session.add(scheduled_post)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            results.append(ScheduleResult(product_id=product.id, outcome="already_scheduled"))
            continue
        product.status = ProductStatus.SCHEDULED
        await session.commit()
        results.append(ScheduleResult(product_id=product.id, outcome="scheduled", scheduled_post_id=scheduled_post.id, scheduled_for=scheduled_for))
    return results
