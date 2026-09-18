"""
Tests for the Phase 2 domain models and their database-level constraints.

These deliberately test the DATABASE's enforcement (unique constraints,
CHECK constraints, partial unique indexes, foreign keys) rather than just
Python-level object construction — the whole point of Phase 2 is that
these rules hold even if application code has a bug, so the tests try to
violate them and assert the database rejects it.

Requires a real Postgres instance (see `db_session` in conftest.py); each
test is skipped automatically if one isn't reachable.
"""

from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models.analytics import Analytics
from app.models.caption import Caption
from app.models.channel import Channel
from app.models.daily_limit import DailyLimit
from app.models.enums import ContentType, MediaType, PostPublishStatus, ProductStatus
from app.models.media import Media
from app.models.product import Product
from app.models.product_source_message import ProductSourceMessage
from app.models.published_post import PublishedPost
from app.models.scan_state import ScanState
from app.models.scheduled_post import ScheduledPost
from app.models.source_message import SourceMessage
from app.models.story import Story
from tests.factories import make_channel, make_source_message, now

pytestmark = pytest.mark.asyncio


async def make_product(db_session, channel, source_message, **overrides) -> Product:
    defaults = dict(
        primary_source_message_id=source_message.id,
        channel_id=channel.id,
        source_channel_id=channel.telegram_channel_id,
        source_channel_name=channel.channel_title,
        source_message_id=source_message.telegram_message_id,
        source_message_date=source_message.message_date,
    )
    defaults.update(overrides)
    product = Product(**defaults)
    db_session.add(product)
    await db_session.flush()
    return product


# --- channels ---------------------------------------------------------


async def test_channel_telegram_id_must_be_unique(db_session):
    await make_channel(db_session, telegram_channel_id=42)
    db_session.add(Channel(telegram_channel_id=42, channel_title="Duplicate"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_channel_status_defaults_to_active(db_session):
    channel = await make_channel(db_session)
    from app.models.enums import ChannelStatus

    assert channel.status == ChannelStatus.ACTIVE


# --- scan_state: one row per channel, restart-safe resume point -------


async def test_scan_state_is_one_per_channel(db_session):
    channel = await make_channel(db_session)
    db_session.add(ScanState(channel_id=channel.id))
    await db_session.flush()

    db_session.add(ScanState(channel_id=channel.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --- source_messages: idempotent ingestion -----------------------------


async def test_source_message_ingestion_is_idempotent_per_channel(db_session):
    channel = await make_channel(db_session)
    await make_source_message(db_session, channel, telegram_message_id=777)

    # Re-ingesting the same Telegram message id for the same channel must
    # be rejected at the DB level, not just avoided by app logic.
    db_session.add(
        SourceMessage(channel_id=channel.id, telegram_message_id=777, message_date=now())
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_same_message_id_allowed_across_different_channels(db_session):
    channel_a = await make_channel(db_session, telegram_channel_id=1)
    channel_b = await make_channel(db_session, telegram_channel_id=2)

    await make_source_message(db_session, channel_a, telegram_message_id=999)
    # Same telegram_message_id, different channel -> fine, no collision.
    await make_source_message(db_session, channel_b, telegram_message_id=999)


# --- products: traceability to a source message ------------------------


async def test_product_is_traceable_to_its_source_message(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message, title="Red Sneakers")

    assert product.status == ProductStatus.DISCOVERED
    assert product.primary_source_message_id == message.id
    assert product.source_channel_id == channel.telegram_channel_id
    assert product.source_message_id == message.telegram_message_id


async def test_product_cannot_reference_a_missing_source_message(db_session):
    channel = await make_channel(db_session)
    db_session.add(
        Product(
            primary_source_message_id=999_999,  # does not exist
            channel_id=channel.id,
            source_channel_id=channel.telegram_channel_id,
            source_channel_name=channel.channel_title,
            source_message_id=1,
            source_message_date=now(),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_same_product_can_link_to_multiple_source_messages(db_session):
    """
    A product discovered from one message can later be linked to
    additional messages it also appeared in (repost / cross-channel
    mirror) without creating a second Product row.
    """
    channel = await make_channel(db_session)
    first_message = await make_source_message(db_session, channel, telegram_message_id=1)
    second_message = await make_source_message(db_session, channel, telegram_message_id=2)
    product = await make_product(db_session, channel, first_message)

    db_session.add(
        ProductSourceMessage(product_id=product.id, source_message_id=first_message.id)
    )
    db_session.add(
        ProductSourceMessage(product_id=product.id, source_message_id=second_message.id)
    )
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(ProductSourceMessage).where(ProductSourceMessage.product_id == product.id)
        )
    ).scalars().all()
    assert len(rows) == 2


async def test_product_source_message_link_cannot_repeat(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    db_session.add(ProductSourceMessage(product_id=product.id, source_message_id=message.id))
    await db_session.flush()

    db_session.add(ProductSourceMessage(product_id=product.id, source_message_id=message.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_product_status_column_rejects_values_outside_the_enum(db_session):
    """
    The 11 lifecycle states are enforced by a native Postgres ENUM type,
    so even a raw SQL insert with an invalid status must fail - this is a
    DB-level guarantee, not just something the ORM/application checks.
    """
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    await db_session.flush()

    with pytest.raises(Exception):
        await db_session.execute(
            text(
                "INSERT INTO products "
                "(primary_source_message_id, channel_id, source_channel_id, "
                "source_channel_name, source_message_id, source_message_date, status) "
                "VALUES (:msg_id, :chan_id, :src_chan_id, 'x', 1, now(), 'NOT_A_REAL_STATUS')"
            ),
            {
                "msg_id": message.id,
                "chan_id": channel.id,
                "src_chan_id": channel.telegram_channel_id,
            },
        )


# --- media: no duplicate file rows per message --------------------------


async def test_media_cannot_duplicate_same_file_for_same_message(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)

    media_kwargs = dict(
        source_message_id=message.id, media_type=MediaType.PHOTO, telegram_file_id="file-abc"
    )
    db_session.add(Media(**media_kwargs))
    await db_session.flush()

    db_session.add(Media(**media_kwargs))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --- captions: at most one selected caption per product -----------------


async def test_only_one_selected_caption_allowed_per_product(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    db_session.add(Caption(product_id=product.id, text="First caption", is_selected=True))
    await db_session.flush()

    db_session.add(Caption(product_id=product.id, text="Second caption", is_selected=True))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_multiple_unselected_caption_drafts_are_allowed(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    db_session.add(Caption(product_id=product.id, text="Draft 1", is_selected=False))
    db_session.add(Caption(product_id=product.id, text="Draft 2", is_selected=False))
    await db_session.flush()


# --- scheduled_posts: POST/REEL only, strong duplicate prevention -------


async def test_scheduled_post_content_type_cannot_be_story(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    db_session.add(
        ScheduledPost(
            product_id=product.id,
            content_type=ContentType.STORY,
            scheduled_for=now(),
            idempotency_key="key-1",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_scheduled_post_prevents_second_active_entry_for_same_product_and_type(
    db_session,
):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    db_session.add(
        ScheduledPost(
            product_id=product.id,
            content_type=ContentType.POST,
            scheduled_for=now(),
            idempotency_key="key-a",
        )
    )
    await db_session.flush()

    # Same product + same content type, still PENDING -> blocked.
    db_session.add(
        ScheduledPost(
            product_id=product.id,
            content_type=ContentType.POST,
            scheduled_for=now(),
            idempotency_key="key-b",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_scheduled_post_allows_new_entry_once_previous_is_no_longer_active(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    db_session.add(
        ScheduledPost(
            product_id=product.id,
            content_type=ContentType.POST,
            status=PostPublishStatus.CANCELLED,
            scheduled_for=now(),
            idempotency_key="key-cancelled",
        )
    )
    await db_session.flush()

    # A CANCELLED row doesn't count as "active", so a new one is allowed.
    db_session.add(
        ScheduledPost(
            product_id=product.id,
            content_type=ContentType.POST,
            scheduled_for=now(),
            idempotency_key="key-new",
        )
    )
    await db_session.flush()


async def test_scheduled_post_idempotency_key_is_unique(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product_a = await make_product(db_session, channel, message)
    message_b = await make_source_message(db_session, channel, telegram_message_id=2)
    product_b = await make_product(db_session, channel, message_b)

    db_session.add(
        ScheduledPost(
            product_id=product_a.id,
            content_type=ContentType.POST,
            scheduled_for=now(),
            idempotency_key="shared-key",
        )
    )
    await db_session.flush()

    db_session.add(
        ScheduledPost(
            product_id=product_b.id,
            content_type=ContentType.REEL,
            scheduled_for=now(),
            idempotency_key="shared-key",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --- stories: repeats are explicitly allowed -----------------------------


async def test_stories_allow_repeats_of_the_same_product(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    db_session.add(Story(product_id=product.id, idempotency_key="story-1"))
    db_session.add(Story(product_id=product.id, idempotency_key="story-2"))
    # No constraint should block two stories for the same product.
    await db_session.flush()

    rows = (
        await db_session.execute(select(Story).where(Story.product_id == product.id))
    ).scalars().all()
    assert len(rows) == 2


# --- daily_limits: one row per date+content_type, MAXIMUM semantics ------


async def test_daily_limit_is_unique_per_date_and_content_type(db_session):
    today = date.today()
    db_session.add(DailyLimit(date=today, content_type=ContentType.POST, max_allowed=5))
    await db_session.flush()

    db_session.add(DailyLimit(date=today, content_type=ContentType.POST, max_allowed=10))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_daily_limit_allows_separate_rows_per_content_type(db_session):
    today = date.today()
    db_session.add(DailyLimit(date=today, content_type=ContentType.POST, max_allowed=5))
    db_session.add(DailyLimit(date=today, content_type=ContentType.REEL, max_allowed=3))
    db_session.add(DailyLimit(date=today, content_type=ContentType.STORY, max_allowed=20))
    await db_session.flush()


# --- analytics: exactly one parent (published_post XOR story) -----------


async def test_analytics_requires_exactly_one_parent(db_session):
    db_session.add(Analytics(metric_date=date.today(), captured_at=now()))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_analytics_rejects_both_parents_at_once(db_session):
    channel = await make_channel(db_session)
    message = await make_source_message(db_session, channel)
    product = await make_product(db_session, channel, message)

    published = PublishedPost(
        product_id=product.id,
        content_type=ContentType.POST,
        published_at=now(),
    )
    story = Story(product_id=product.id, idempotency_key="story-analytics")
    db_session.add_all([published, story])
    await db_session.flush()

    db_session.add(
        Analytics(
            published_post_id=published.id,
            story_id=story.id,
            metric_date=date.today(),
            captured_at=now(),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
