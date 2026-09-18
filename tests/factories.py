"""
Small helpers for building valid model instances in tests.

These build the minimum valid row for each entity so individual tests can
focus on the one constraint/relationship they're checking instead of
repeating boilerplate setup.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import Analytics
from app.models.caption import Caption
from app.models.channel import Channel
from app.models.daily_limit import DailyLimit
from app.models.enums import (
    CaptionValidationStatus,
    ContentType,
    MediaType,
    PostPublishStatus,
    ProductStatus,
    PublishedContentStatus,
    StoryStatus,
)
from app.models.media import Media
from app.models.product import Product
from app.models.published_post import PublishedPost
from app.models.scheduled_post import ScheduledPost
from app.models.source_message import SourceMessage
from app.models.story import Story


def now() -> datetime:
    return datetime.now(timezone.utc)


async def make_channel(
    db_session: AsyncSession, telegram_channel_id: int = 1001, title: str = "Test Channel"
) -> Channel:
    channel = Channel(
        telegram_channel_id=telegram_channel_id,
        channel_username="testchannel",
        channel_title=title,
    )
    db_session.add(channel)
    await db_session.flush()
    return channel


async def make_source_message(
    db_session: AsyncSession,
    channel: Channel,
    telegram_message_id: int = 5001,
    raw_text: str | None = "Sample product text",
    message_date: datetime | None = None,
) -> SourceMessage:
    message = SourceMessage(
        channel_id=channel.id,
        telegram_message_id=telegram_message_id,
        message_date=message_date or now(),
        raw_text=raw_text,
    )
    db_session.add(message)
    await db_session.flush()
    return message


async def make_product(
    db_session: AsyncSession,
    channel: Channel,
    source_message: SourceMessage,
    *,
    status: ProductStatus = ProductStatus.VALIDATED,
    title: str = "Test Product",
    description: str | None = "A reasonably detailed test product description for scoring.",
    price: Decimal | None = Decimal("10.00"),
    currency: str | None = "KWD",
    content_hash: str | None = None,
    is_duplicate_of: int | None = None,
    score: int | None = None,
) -> Product:
    """Builds a Product directly (not via `product_service.process_source_message`)
    so Phase 6 tests can set `status`/`price`/`description` precisely
    without re-running the full Phase 4 parsing pipeline for every case."""
    product = Product(
        primary_source_message_id=source_message.id,
        channel_id=channel.id,
        source_channel_id=channel.telegram_channel_id,
        source_channel_name=channel.channel_title,
        source_message_id=source_message.telegram_message_id,
        source_message_date=source_message.message_date,
        source_message_link=None,
        status=status,
        title=title,
        description=description,
        price=price,
        currency=currency,
        content_hash=content_hash or f"hash-{uuid4().hex}",
        is_duplicate_of=is_duplicate_of,
        score=score,
    )
    db_session.add(product)
    await db_session.flush()
    return product


async def make_caption(
    db_session: AsyncSession,
    product: Product,
    *,
    text: str = "Test caption text, message us for details!",
    version: int = 1,
    is_selected: bool = True,
    validation_status: CaptionValidationStatus = CaptionValidationStatus.PASSED,
    rejection_reason: str | None = None,
    generated_by: str | None = "test-model",
) -> Caption:
    caption = Caption(
        product_id=product.id,
        text=text,
        version=version,
        is_selected=is_selected,
        validation_status=validation_status,
        rejection_reason=rejection_reason,
        generated_by=generated_by,
    )
    db_session.add(caption)
    await db_session.flush()
    return caption


async def make_media(
    db_session: AsyncSession,
    source_message: SourceMessage,
    *,
    product: Product | None = None,
    media_type: MediaType = MediaType.PHOTO,
    file_path: str | None = "/mnt/media/test.jpg",
    telegram_file_id: str = "file-1",
    display_order: int = 0,
) -> Media:
    media = Media(
        source_message_id=source_message.id,
        product_id=product.id if product is not None else None,
        media_type=media_type,
        telegram_file_id=telegram_file_id,
        file_path=file_path,
        display_order=display_order,
    )
    db_session.add(media)
    await db_session.flush()
    return media


async def make_published_post(
    db_session: AsyncSession,
    product: Product,
    *,
    content_type: ContentType = ContentType.POST,
    published_at: datetime | None = None,
    status: PublishedContentStatus = PublishedContentStatus.LIVE,
    instagram_media_id: str | None = None,
) -> PublishedPost:
    post = PublishedPost(
        product_id=product.id,
        content_type=content_type,
        published_at=published_at or now(),
        status=status,
        instagram_media_id=instagram_media_id,
    )
    db_session.add(post)
    await db_session.flush()
    return post


async def make_story(
    db_session: AsyncSession,
    product: Product,
    *,
    published_at: datetime | None = None,
    status: StoryStatus = StoryStatus.PUBLISHED,
    idempotency_key: str | None = None,
) -> Story:
    story = Story(
        product_id=product.id,
        status=status,
        published_at=published_at,
        idempotency_key=idempotency_key or f"story-{uuid4().hex}",
    )
    db_session.add(story)
    await db_session.flush()
    return story


async def make_analytics(
    db_session: AsyncSession,
    *,
    published_post: PublishedPost | None = None,
    story: Story | None = None,
    metric_date=None,
    impressions: int | None = None,
    reach: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    saves: int | None = None,
    engagement_rate: Decimal | None = None,
    captured_at: datetime | None = None,
) -> Analytics:
    row = Analytics(
        published_post_id=published_post.id if published_post is not None else None,
        story_id=story.id if story is not None else None,
        metric_date=metric_date or now().date(),
        impressions=impressions,
        reach=reach,
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
        engagement_rate=engagement_rate,
        captured_at=captured_at or now(),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def make_scheduled_post(
    db_session: AsyncSession,
    product: Product,
    *,
    content_type: ContentType = ContentType.POST,
    caption: Caption | None = None,
    status: PostPublishStatus = PostPublishStatus.SCHEDULED,
    scheduled_for: datetime | None = None,
    attempt_count: int = 0,
    last_attempt_at: datetime | None = None,
    last_error: str | None = None,
    idempotency_key: str | None = None,
) -> ScheduledPost:
    scheduled_post = ScheduledPost(
        product_id=product.id,
        content_type=content_type,
        caption_id=caption.id if caption is not None else None,
        status=status,
        scheduled_for=scheduled_for or now(),
        attempt_count=attempt_count,
        last_attempt_at=last_attempt_at,
        last_error=last_error,
        idempotency_key=idempotency_key or f"sp-{uuid4().hex}",
    )
    db_session.add(scheduled_post)
    await db_session.flush()
    return scheduled_post


async def make_daily_limit(
    db_session: AsyncSession,
    *,
    date=None,
    content_type: ContentType = ContentType.POST,
    max_allowed: int = 5,
    published_count: int = 0,
) -> DailyLimit:
    limit = DailyLimit(
        date=date or now().date(),
        content_type=content_type,
        max_allowed=max_allowed,
        published_count=published_count,
    )
    db_session.add(limit)
    await db_session.flush()
    return limit
