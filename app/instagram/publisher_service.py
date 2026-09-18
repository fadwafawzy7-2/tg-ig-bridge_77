"""
Orchestrates ONE `scheduled_posts` row (POST/REEL) or ONE `stories` row
into an actual, confirmed Instagram publication, end to end:

    safety gate -> claim (concurrency-safe) -> daily-limit reservation
    -> resolve media URL(s) -> Graph API container/poll/publish
    -> persist published_posts / update stories -> release reservation
       on any failure, keep it only on confirmed success

Mirrors every prior phase's service module: one item's failure is
isolated (logged to `errors` with source=`instagram_publisher`, recorded
on the row via `attempt_count`/`last_attempt_at`/`last_error`), never
raised out of a batch run — see `runner.py`.

Concurrency/duplicate-safety, concretely:
- `_claim()` is a single atomic conditional `UPDATE ... WHERE status IN
  (PENDING, SCHEDULED) -> PROCESSING`, so two workers can never both
  process the same row (mirrors `daily_limit_guard.reserve_slot`'s
  pattern).
- `daily_limit_guard.reserve_slot`/`release_slot` guarantee the daily
  MAXIMUM is never exceeded even under concurrent publishers (see that
  module's docstring) — reserved BEFORE calling Instagram, released if
  the attempt does not end in confirmed success.
- `published_posts.scheduled_post_id` is UNIQUE (Phase 2) — a genuine
  duplicate insert attempt (e.g. a race this code's own checks somehow
  missed) fails at the DB level and is treated as "already published",
  never silently creating a second publication.
- Nothing is EVER marked PUBLISHED (`scheduled_posts.status`,
  `products.status`, `daily_limits.published_count`) except on a
  confirmed Instagram media id returned by `client.publish_container()`.
  A failed/timed-out attempt only ever updates `attempt_count`/
  `last_error` and, if permanent or out of attempts, `status=FAILED` —
  never PUBLISHED.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date as date_
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.instagram.client import InstagramAPIError, InstagramClient, InstagramMediaError, InstagramTimeoutError
from app.instagram.daily_limit_guard import release_slot, reserve_slot
from app.instagram.media_resolver import resolve_media_url
from app.instagram.retry_policy import has_attempts_remaining, is_due_for_retry
from app.instagram.safety import check_publish_safety
from app.models.caption import Caption
from app.models.enums import ContentType, MediaType, PostPublishStatus, ProductStatus, PublishedContentStatus, StoryStatus
from app.models.error_log import ErrorLog
from app.models.media import Media
from app.models.product import Product
from app.models.published_post import PublishedPost
from app.models.scheduled_post import ScheduledPost
from app.models.story import Story
from app.queue.eligibility import is_valid_media_item
from app.queue.time_utils import utcnow

logger = logging.getLogger(__name__)

ERROR_SOURCE = "instagram_publisher"

# Instagram's own carousel limit.
MAX_CAROUSEL_ITEMS = 10


@dataclass(frozen=True)
class PublishOutcome:
    entity_type: str  # "scheduled_post" | "story"
    entity_id: int
    outcome: str
    instagram_media_id: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "published"


# --- Shared helpers --------------------------------------------------


async def _load_valid_media(
    session: AsyncSession, product_id: int, wanted_type: MediaType
) -> list[Media]:
    result = await session.execute(
        select(Media).where(Media.product_id == product_id).order_by(Media.display_order)
    )
    items = result.scalars().all()
    return [
        m
        for m in items
        if m.media_type == wanted_type and is_valid_media_item(media_type=m.media_type, file_path=m.file_path)
    ]


async def _create_and_finish_container(
    client: InstagramClient,
    *,
    media_url: str | None,
    media_type: str | None,
    caption: str | None = None,
    is_carousel_item: bool = False,
    children: list[str] | None = None,
    poll_interval: float,
    poll_max_attempts: int,
) -> str:
    container_id = await client.create_container(
        media_url=media_url or "",
        media_type=media_type,
        caption=caption,
        is_carousel_item=is_carousel_item,
        children=children,
    )
    for _ in range(poll_max_attempts):
        status = await client.get_container_status(container_id)
        if status == "FINISHED":
            return container_id
        if status in ("ERROR", "EXPIRED"):
            raise InstagramMediaError(f"container {container_id} ended in status {status}")
        await asyncio.sleep(poll_interval)
    raise InstagramTimeoutError(f"container {container_id} did not finish processing in time")


async def _publish_post_or_reel(
    client: InstagramClient,
    *,
    media_items: list[Media],
    caption_text: str | None,
    content_type: ContentType,
    poll_interval: float,
    poll_max_attempts: int,
) -> str:
    """Returns the confirmed Instagram media id."""
    if content_type == ContentType.REEL:
        url = resolve_media_url(file_path=media_items[0].file_path)
        container_id = await _create_and_finish_container(
            client,
            media_url=url,
            media_type="REELS",
            caption=caption_text,
            poll_interval=poll_interval,
            poll_max_attempts=poll_max_attempts,
        )
    elif len(media_items) == 1:
        url = resolve_media_url(file_path=media_items[0].file_path)
        container_id = await _create_and_finish_container(
            client,
            media_url=url,
            media_type=None,
            caption=caption_text,
            poll_interval=poll_interval,
            poll_max_attempts=poll_max_attempts,
        )
    else:
        child_ids: list[str] = []
        for item in media_items[:MAX_CAROUSEL_ITEMS]:
            url = resolve_media_url(file_path=item.file_path)
            child_id = await _create_and_finish_container(
                client,
                media_url=url,
                media_type=None,
                is_carousel_item=True,
                poll_interval=poll_interval,
                poll_max_attempts=poll_max_attempts,
            )
            child_ids.append(child_id)
        container_id = await _create_and_finish_container(
            client,
            media_url=None,
            media_type="CAROUSEL",
            caption=caption_text,
            children=child_ids,
            poll_interval=poll_interval,
            poll_max_attempts=poll_max_attempts,
        )

    return await client.publish_container(container_id)


def _log_error(session: AsyncSession, *, message: str, product_id: int | None) -> None:
    from app.models.enums import ErrorSeverity

    session.add(
        ErrorLog(source=ERROR_SOURCE, severity=ErrorSeverity.ERROR, message=message, product_id=product_id)
    )


# --- scheduled_posts (POST/REEL) --------------------------------------


async def _claim_scheduled_post(session: AsyncSession, scheduled_post_id: int) -> ScheduledPost | None:
    stmt = (
        update(ScheduledPost)
        .where(
            ScheduledPost.id == scheduled_post_id,
            ScheduledPost.status.in_((PostPublishStatus.PENDING, PostPublishStatus.SCHEDULED)),
        )
        .values(status=PostPublishStatus.PROCESSING)
        .returning(ScheduledPost.id)
    )
    result = await session.execute(stmt)
    claimed_id = result.scalar_one_or_none()
    if claimed_id is None:
        await session.rollback()
        return None
    await session.commit()
    return await session.get(ScheduledPost, scheduled_post_id)


async def _revert_to_scheduled(session: AsyncSession, scheduled_post: ScheduledPost) -> None:
    scheduled_post.status = PostPublishStatus.SCHEDULED
    await session.commit()


async def _record_scheduled_post_failure(
    session: AsyncSession,
    scheduled_post: ScheduledPost,
    *,
    message: str,
    transient: bool,
    product_id: int,
) -> str:
    settings = get_settings()
    scheduled_post.attempt_count += 1
    scheduled_post.last_attempt_at = utcnow()
    scheduled_post.last_error = message

    will_retry = transient and has_attempts_remaining(
        attempt_count=scheduled_post.attempt_count, max_attempts=settings.INSTAGRAM_MAX_PUBLISH_ATTEMPTS
    )
    scheduled_post.status = PostPublishStatus.SCHEDULED if will_retry else PostPublishStatus.FAILED
    if not will_retry:
        product = await session.get(Product, product_id)
        if product is not None:
            product.status = ProductStatus.FAILED

    _log_error(session, message=message, product_id=product_id)
    await session.commit()
    return "retrying" if will_retry else "failed_permanent"


def build_instagram_caption(caption_text: str, product_code: str) -> str:
    """Append the stable product code at the final publish boundary.

    The AI never generates this metadata. It is deterministic and therefore
    cannot be omitted accidentally by a model response.
    """
    base = caption_text.strip()
    suffix = f"\n\nرمز المنتج: {product_code}"
    return base + suffix


async def publish_scheduled_post(
    session: AsyncSession, scheduled_post_id: int, client: InstagramClient
) -> PublishOutcome:
    settings = get_settings()

    # Idempotency short-circuit: a PublishedPost already exists -> done,
    # never re-publish (published_posts.scheduled_post_id is UNIQUE too,
    # as a DB-level backstop for this same check).
    already = (
        await session.execute(
            select(PublishedPost.id).where(PublishedPost.scheduled_post_id == scheduled_post_id)
        )
    ).scalars().first()
    if already is not None:
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="already_published")

    scheduled_post = await session.get(ScheduledPost, scheduled_post_id)
    if scheduled_post is None:
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="not_found")

    if scheduled_post.status == PostPublishStatus.PUBLISHED:
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="already_published")
    if scheduled_post.status == PostPublishStatus.CANCELLED:
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="cancelled")
    if scheduled_post.status == PostPublishStatus.FAILED:
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="failed_permanent")

    if not is_due_for_retry(
        attempt_count=scheduled_post.attempt_count,
        last_attempt_at=scheduled_post.last_attempt_at,
        now=utcnow(),
        base_delay=settings.INSTAGRAM_RETRY_BASE_DELAY_SECONDS,
        max_delay=settings.INSTAGRAM_RETRY_MAX_DELAY_SECONDS,
    ):
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="not_due_for_retry")

    claimed = await _claim_scheduled_post(session, scheduled_post_id)
    if claimed is None:
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="already_claimed")
    scheduled_post = claimed

    product = await session.get(Product, scheduled_post.product_id)
    caption = await session.get(Caption, scheduled_post.caption_id) if scheduled_post.caption_id else None
    await session.refresh(product, attribute_names=["primary_source_message"])
    raw_text = product.primary_source_message.raw_text if product.primary_source_message else None

    safety = check_publish_safety(
        product_status=product.status.value,
        is_duplicate=product.is_duplicate_of is not None,
        caption_text=caption.text if caption is not None else None,
        caption_is_selected=caption.is_selected if caption is not None else False,
        caption_validation_status=caption.validation_status.value if caption is not None else None,
        raw_text=raw_text,
        price=product.price,
        currency=product.currency,
        product_code=product.product_code,
    )
    if not safety.is_safe:
        message = f"Refusing to publish product {product.id}: " + "; ".join(safety.reasons)
        outcome = await _record_scheduled_post_failure(
            session, scheduled_post, message=message, transient=False, product_id=product.id
        )
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="unsafe", error=message)

    wanted_media_type = MediaType.VIDEO if scheduled_post.content_type == ContentType.REEL else MediaType.PHOTO
    media_items = await _load_valid_media(session, product.id, wanted_media_type)
    if not media_items:
        message = f"No valid {wanted_media_type.value} media available for product {product.id}"
        await _record_scheduled_post_failure(
            session, scheduled_post, message=message, transient=False, product_id=product.id
        )
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="unsafe", error=message)

    tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)
    target_date: date_ = scheduled_post.scheduled_for.astimezone(tz).date()

    reserved = await reserve_slot(session, target_date=target_date, content_type=scheduled_post.content_type)
    if not reserved:
        await _revert_to_scheduled(session, scheduled_post)
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="limit_reached")

    try:
        instagram_media_id = await asyncio.wait_for(
            _publish_post_or_reel(
                client,
                media_items=media_items,
                caption_text=build_instagram_caption(caption.text, product.product_code) if caption is not None else None,
                content_type=scheduled_post.content_type,
                poll_interval=settings.INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS,
                poll_max_attempts=settings.INSTAGRAM_CONTAINER_POLL_MAX_ATTEMPTS,
            ),
            timeout=settings.INSTAGRAM_REQUEST_TIMEOUT_SECONDS * max(1, len(media_items) + 2),
        )
    except asyncio.TimeoutError:
        await release_slot(session, target_date=target_date, content_type=scheduled_post.content_type)
        message = f"Publishing product {product.id} timed out"
        outcome = await _record_scheduled_post_failure(
            session, scheduled_post, message=message, transient=True, product_id=product.id
        )
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome=outcome, error=message)
    except AppError as exc:
        await release_slot(session, target_date=target_date, content_type=scheduled_post.content_type)
        transient = getattr(exc, "is_transient", False)
        message = str(exc)
        outcome = await _record_scheduled_post_failure(
            session, scheduled_post, message=message, transient=transient, product_id=product.id
        )
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome=outcome, error=message)
    except Exception as exc:  # noqa: BLE001 - unrecognized error: isolate, do not retry blindly
        await release_slot(session, target_date=target_date, content_type=scheduled_post.content_type)
        message = f"Unexpected error publishing product {product.id}: {exc}"
        outcome = await _record_scheduled_post_failure(
            session, scheduled_post, message=message, transient=False, product_id=product.id
        )
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome=outcome, error=message)

    published_post = PublishedPost(
        scheduled_post_id=scheduled_post.id,
        product_id=product.id,
        caption_id=caption.id if caption is not None else None,
        content_type=scheduled_post.content_type,
        instagram_media_id=instagram_media_id,
        published_at=utcnow(),
        status=PublishedContentStatus.LIVE,
    )
    session.add(published_post)
    scheduled_post.status = PostPublishStatus.PUBLISHED
    scheduled_post.attempt_count += 1
    scheduled_post.last_attempt_at = utcnow()
    scheduled_post.last_error = None
    product.status = ProductStatus.PUBLISHED

    try:
        await session.commit()
    except IntegrityError:
        # A genuine duplicate slipped through every earlier check (should
        # not happen) - the DB's own uniqueness is the final backstop.
        await session.rollback()
        return PublishOutcome(entity_type="scheduled_post", entity_id=scheduled_post_id, outcome="already_published")

    return PublishOutcome(
        entity_type="scheduled_post",
        entity_id=scheduled_post_id,
        outcome="published",
        instagram_media_id=instagram_media_id,
    )


# --- stories -----------------------------------------------------------
#
# NOTE on retry timing for stories: unlike `scheduled_posts`, `stories`
# has no `last_attempt_at` column (see `app/models/story.py`), so
# backoff-based "is this due for retry yet" timing (`retry_policy.
# is_due_for_retry`) cannot be applied here — only `attempt_count` vs
# `INSTAGRAM_MAX_PUBLISH_ATTEMPTS` is enforced. Adding `last_attempt_at`
# to `stories` would need a migration; given Stories are explicitly a
# conditional, secondary part of this phase's scope ("فقط إذا كانت
# مدعومة فعليًا"), that schema change was judged not necessary for this
# phase — documented here rather than silently working around it.


async def _claim_story(session: AsyncSession, story_id: int) -> Story | None:
    stmt = (
        update(Story)
        .where(Story.id == story_id, Story.status.in_((StoryStatus.PENDING, StoryStatus.SCHEDULED)))
        .values(status=StoryStatus.PROCESSING)
        .returning(Story.id)
    )
    result = await session.execute(stmt)
    claimed_id = result.scalar_one_or_none()
    if claimed_id is None:
        await session.rollback()
        return None
    await session.commit()
    return await session.get(Story, story_id)


async def _record_story_failure(
    session: AsyncSession, story: Story, *, message: str, transient: bool, product_id: int
) -> str:
    settings = get_settings()
    story.attempt_count += 1
    story.last_error = message

    will_retry = transient and has_attempts_remaining(
        attempt_count=story.attempt_count, max_attempts=settings.INSTAGRAM_MAX_PUBLISH_ATTEMPTS
    )
    story.status = (
        (StoryStatus.SCHEDULED if story.scheduled_for is not None else StoryStatus.PENDING)
        if will_retry
        else StoryStatus.FAILED
    )
    _log_error(session, message=message, product_id=product_id)
    await session.commit()
    return "retrying" if will_retry else "failed_permanent"


async def publish_story(session: AsyncSession, story_id: int, client: InstagramClient) -> PublishOutcome:
    settings = get_settings()

    story = await session.get(Story, story_id)
    if story is None:
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="not_found")

    if story.instagram_story_id is not None or story.status == StoryStatus.PUBLISHED:
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="already_published")
    if story.status == StoryStatus.CANCELLED:
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="cancelled")
    if story.status == StoryStatus.FAILED:
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="failed_permanent")

    if not has_attempts_remaining(
        attempt_count=story.attempt_count, max_attempts=settings.INSTAGRAM_MAX_PUBLISH_ATTEMPTS
    ):
        story.status = StoryStatus.FAILED
        await session.commit()
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="failed_permanent")

    claimed = await _claim_story(session, story_id)
    if claimed is None:
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="already_claimed")
    story = claimed

    product = await session.get(Product, story.product_id)
    if (
        product.status in (ProductStatus.DUPLICATE, ProductStatus.REJECTED, ProductStatus.SKIPPED)
        or product.is_duplicate_of is not None
    ):
        message = f"Refusing to publish story for product {product.id}: product status is {product.status.value}"
        await _record_story_failure(session, story, message=message, transient=False, product_id=product.id)
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="unsafe", error=message)

    media_item = await session.get(Media, story.media_id) if story.media_id is not None else None
    if media_item is None or not is_valid_media_item(
        media_type=media_item.media_type, file_path=media_item.file_path
    ):
        message = f"No valid media available for story {story.id}"
        await _record_story_failure(session, story, message=message, transient=False, product_id=product.id)
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="unsafe", error=message)

    tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)
    target_date: date_ = (story.scheduled_for or utcnow()).astimezone(tz).date()

    reserved = await reserve_slot(session, target_date=target_date, content_type=ContentType.STORY)
    if not reserved:
        story.status = StoryStatus.SCHEDULED if story.scheduled_for is not None else StoryStatus.PENDING
        await session.commit()
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="limit_reached")

    try:
        url = resolve_media_url(file_path=media_item.file_path)
        container_id = await asyncio.wait_for(
            _create_and_finish_container(
                client,
                media_url=url,
                media_type="STORIES",
                poll_interval=settings.INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS,
                poll_max_attempts=settings.INSTAGRAM_CONTAINER_POLL_MAX_ATTEMPTS,
            ),
            timeout=settings.INSTAGRAM_REQUEST_TIMEOUT_SECONDS * 3,
        )
        instagram_story_id = await client.publish_container(container_id)
    except asyncio.TimeoutError:
        await release_slot(session, target_date=target_date, content_type=ContentType.STORY)
        message = f"Publishing story {story.id} timed out"
        outcome = await _record_story_failure(session, story, message=message, transient=True, product_id=product.id)
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome=outcome, error=message)
    except AppError as exc:
        await release_slot(session, target_date=target_date, content_type=ContentType.STORY)
        transient = getattr(exc, "is_transient", False)
        message = str(exc)
        outcome = await _record_story_failure(
            session, story, message=message, transient=transient, product_id=product.id
        )
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome=outcome, error=message)
    except Exception as exc:  # noqa: BLE001 - unrecognized error: isolate, do not retry blindly
        await release_slot(session, target_date=target_date, content_type=ContentType.STORY)
        message = f"Unexpected error publishing story {story.id}: {exc}"
        outcome = await _record_story_failure(session, story, message=message, transient=False, product_id=product.id)
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome=outcome, error=message)

    from datetime import timedelta

    story.instagram_story_id = instagram_story_id
    story.status = StoryStatus.PUBLISHED
    story.published_at = utcnow()
    story.expires_at = utcnow() + timedelta(hours=24)
    story.attempt_count += 1
    story.last_error = None
    # Deliberately NOT touching product.status here - stories allow
    # repeats (see story.py's docstring), so publishing a story must not
    # force the product into the terminal PUBLISHED state the way a
    # POST/REEL does.

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return PublishOutcome(entity_type="story", entity_id=story_id, outcome="already_published")

    return PublishOutcome(
        entity_type="story", entity_id=story_id, outcome="published", instagram_media_id=instagram_story_id
    )
