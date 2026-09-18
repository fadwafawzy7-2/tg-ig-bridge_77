"""
The five Phase 6 domain operations — explicit, imperative actions taken
on ONE product at a time (by an operator, or a future admin API/CLI),
distinct from the automatic batch pipeline
(`eligibility_service.py` -> `queue_service.py` -> `scheduler.py`) that
processes everything at once.

None of these call the Instagram API. `publish_now` still only creates a
`scheduled_posts` row with `scheduled_for` set to "right now" in the
scheduler timezone — it does not itself publish anything, and it is
still subject to the same `daily_limits` MAXIMUM as the batch scheduler
(a manual "publish now" request is never allowed to exceed the day's
ceiling). Actually executing a publish and marking
`scheduled_posts.status=PUBLISHED` / `products.status=PUBLISHED` is a
later phase's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.models.enums import ContentType, PostPublishStatus, ProductStatus
from app.models.product import Product
from app.models.scheduled_post import ScheduledPost
from app.queue.scheduler import (
    build_idempotency_key,
    remaining_capacity,
    selected_caption_id,
)
from app.queue.time_utils import now_in_timezone

logger = logging.getLogger(__name__)

# Statuses a product must be in for `schedule`/`publish_now` to make
# sense — anything already SCHEDULED/PUBLISHED/SKIPPED is not
# re-schedulable through this path (use `retry_failed` for a FAILED one).
_SCHEDULABLE_STATUSES = (ProductStatus.ELIGIBLE, ProductStatus.QUEUED)


@dataclass(frozen=True)
class OperationResult:
    product_id: int
    operation: str
    outcome: str
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome not in {
            "invalid_status",
            "limit_reached",
            "no_daily_limit_configured",
            "nothing_to_retry",
        }


async def _create_scheduled_post(
    session: AsyncSession,
    product: Product,
    *,
    content_type: ContentType,
    scheduled_for,
    operation_name: str,
) -> OperationResult:
    if scheduled_for.tzinfo is None:
        raise ValidationError("scheduled_for must be timezone-aware")

    settings = get_settings()
    scheduler_tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)
    # Bucketed by the SCHEDULER's calendar day, not whatever timezone the
    # caller happened to express `scheduled_for` in — this is what keeps
    # daily_limits counting consistent with the batch scheduler
    # (`scheduler.py`), which always buckets by this same timezone.
    target_date = scheduled_for.astimezone(scheduler_tz).date()

    remaining = await remaining_capacity(
        session, target_date=target_date, content_type=content_type, tz_name=settings.SCHEDULER_TIMEZONE
    )
    if remaining is None:
        return OperationResult(
            product_id=product.id,
            operation=operation_name,
            outcome="no_daily_limit_configured",
            detail=f"no daily_limits row for date={target_date} content_type={content_type.value}",
        )
    if remaining <= 0:
        return OperationResult(
            product_id=product.id,
            operation=operation_name,
            outcome="limit_reached",
            detail=f"daily limit already reached for date={target_date} content_type={content_type.value}",
        )

    idempotency_key = build_idempotency_key(
        product_id=product.id, content_type=content_type, target_date=target_date
    )
    caption_id = await selected_caption_id(session, product.id)

    scheduled_post = ScheduledPost(
        product_id=product.id,
        content_type=content_type,
        caption_id=caption_id,
        status=PostPublishStatus.SCHEDULED,
        scheduled_for=scheduled_for,
        idempotency_key=idempotency_key,
    )
    session.add(scheduled_post)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return OperationResult(
            product_id=product.id, operation=operation_name, outcome="already_scheduled"
        )

    product.status = ProductStatus.SCHEDULED
    await session.commit()
    return OperationResult(
        product_id=product.id,
        operation=operation_name,
        outcome="scheduled",
        detail=f"scheduled_post_id={scheduled_post.id} scheduled_for={scheduled_for.isoformat()}",
    )


async def schedule(
    session: AsyncSession,
    product: Product,
    *,
    content_type: ContentType | None = None,
    scheduled_for=None,
) -> OperationResult:
    """Manually schedule ONE product. Defaults `content_type` to
    `settings.SCHEDULER_DEFAULT_CONTENT_TYPE` and `scheduled_for` to "now"
    (in `settings.SCHEDULER_TIMEZONE`) when not given explicitly. Still
    subject to the day's `daily_limits` ceiling like the batch scheduler."""
    if product.status not in _SCHEDULABLE_STATUSES:
        return OperationResult(
            product_id=product.id,
            operation="schedule",
            outcome="invalid_status",
            detail=f"product status is {product.status.value}, expected ELIGIBLE or QUEUED",
        )

    settings = get_settings()
    resolved_content_type = content_type or ContentType(settings.SCHEDULER_DEFAULT_CONTENT_TYPE)
    resolved_time = scheduled_for or now_in_timezone(settings.SCHEDULER_TIMEZONE)

    return await _create_scheduled_post(
        session,
        product,
        content_type=resolved_content_type,
        scheduled_for=resolved_time,
        operation_name="schedule",
    )


async def publish_now(
    session: AsyncSession, product: Product, *, content_type: ContentType | None = None
) -> OperationResult:
    """Fast-track a product to be scheduled for RIGHT NOW (in
    `settings.SCHEDULER_TIMEZONE`), bypassing normal slot spacing — but
    NOT bypassing the daily limit MAXIMUM, and NOT itself calling
    Instagram (see module docstring)."""
    if product.status not in _SCHEDULABLE_STATUSES:
        return OperationResult(
            product_id=product.id,
            operation="publish_now",
            outcome="invalid_status",
            detail=f"product status is {product.status.value}, expected ELIGIBLE or QUEUED",
        )

    settings = get_settings()
    resolved_content_type = content_type or ContentType(settings.SCHEDULER_DEFAULT_CONTENT_TYPE)
    now = now_in_timezone(settings.SCHEDULER_TIMEZONE)

    return await _create_scheduled_post(
        session,
        product,
        content_type=resolved_content_type,
        scheduled_for=now,
        operation_name="publish_now",
    )


async def skip(session: AsyncSession, product: Product, *, reason: str | None = None) -> OperationResult:
    """Mark a product as SKIPPED — an operator's decision to never
    publish it (or not for now). Cancels any active scheduled post so its
    daily_limits capacity is freed for another product."""
    if product.status in (ProductStatus.PUBLISHED, ProductStatus.SKIPPED):
        return OperationResult(
            product_id=product.id,
            operation="skip",
            outcome="invalid_status",
            detail=f"product status is already {product.status.value}",
        )

    for scheduled_post in await _active_scheduled_posts(session, product.id):
        scheduled_post.status = PostPublishStatus.CANCELLED

    product.status = ProductStatus.SKIPPED
    if reason:
        product.rejection_reason = f"Skipped: {reason}"
    await session.commit()
    return OperationResult(product_id=product.id, operation="skip", outcome="skipped")


async def retry_failed(session: AsyncSession, product: Product) -> OperationResult:
    """Requeue a FAILED product/scheduled-post for another attempt.

    Prefers un-failing the most recent FAILED `scheduled_posts` row for
    this product (putting it back to SCHEDULED at its same slot) if one
    exists; otherwise, if the product itself is at FAILED, resets it to
    ELIGIBLE so the normal pipeline (which re-checks caption/media from
    scratch) picks it up again from there.
    """
    failed_post = await _most_recent_failed_scheduled_post(session, product.id)
    if failed_post is not None:
        failed_post.status = PostPublishStatus.SCHEDULED
        failed_post.last_error = None
        product.status = ProductStatus.SCHEDULED
        await session.commit()
        return OperationResult(
            product_id=product.id,
            operation="retry_failed",
            outcome="rescheduled",
            detail=f"scheduled_post_id={failed_post.id}",
        )

    if product.status == ProductStatus.FAILED:
        product.status = ProductStatus.ELIGIBLE
        await session.commit()
        return OperationResult(
            product_id=product.id, operation="retry_failed", outcome="reset_to_eligible"
        )

    return OperationResult(product_id=product.id, operation="retry_failed", outcome="nothing_to_retry")


async def reprioritize(session: AsyncSession, product: Product, new_score: int) -> OperationResult:
    """Manually override `products.score` (0-100) — e.g. to bump a
    product up/down in queue ordering regardless of what the automatic
    scoring engine (`scoring.py`) computed for it. Does not change
    `products.status`."""
    if not (0 <= new_score <= 100):
        raise ValidationError(f"score must be between 0 and 100, got {new_score}")

    product.score = new_score
    await session.commit()
    return OperationResult(
        product_id=product.id, operation="reprioritize", outcome="reprioritized", detail=f"score={new_score}"
    )


async def _active_scheduled_posts(session: AsyncSession, product_id: int) -> list[ScheduledPost]:
    from sqlalchemy import select

    from app.queue.scheduler import ACTIVE_SCHEDULED_POST_STATUSES

    result = await session.execute(
        select(ScheduledPost).where(
            ScheduledPost.product_id == product_id,
            ScheduledPost.status.in_(ACTIVE_SCHEDULED_POST_STATUSES),
        )
    )
    return list(result.scalars().all())


async def _most_recent_failed_scheduled_post(
    session: AsyncSession, product_id: int
) -> ScheduledPost | None:
    from sqlalchemy import select

    result = await session.execute(
        select(ScheduledPost)
        .where(ScheduledPost.product_id == product_id, ScheduledPost.status == PostPublishStatus.FAILED)
        .order_by(ScheduledPost.id.desc())
    )
    return result.scalars().first()
