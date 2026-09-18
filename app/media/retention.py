"""Storage-retention cleanup for finished products.

Deletes media files (photos/videos, on local disk or R2) and generated
captions for products that have reached a terminal status and are older
than `settings.MEDIA_RETENTION_DAYS`. The `Product` row itself is
deliberately never deleted — `product_code`, `source_channel_name`,
`source_message_id`, `title`, etc. are kept forever, so the dashboard
bot's "send me the product code" lookup (see
`app.dashboard.service.product_by_code`) keeps working indefinitely even
after the underlying media has been reclaimed to save storage.

Only products in a terminal status are touched. A product that is still
DISCOVERED/PARSED/VALIDATED/ELIGIBLE/QUEUED/SCHEDULED is left alone even
if it is older than the retention window, because the pipeline still
needs its media to actually publish it.

Trade-off worth knowing: `app.queue.scheduler.schedule_stories` can turn
an already-PUBLISHED product into a Story later on. Once this job has
purged a product's media, no further Story can be created from it. This
is accepted as intentional, since the whole point of this job is to
reclaim storage from finished products.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.media import cloud_storage
from app.media.storage import safe_relative_path, storage_root
from app.models.caption import Caption
from app.models.enums import ProductStatus
from app.models.media import Media
from app.models.product import Product

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = (
    ProductStatus.PUBLISHED,
    ProductStatus.SKIPPED,
    ProductStatus.DUPLICATE,
    ProductStatus.REJECTED,
    ProductStatus.FAILED,
)


@dataclass(frozen=True)
class RetentionSummary:
    products_purged: int
    media_files_deleted: int
    media_files_failed: int
    captions_deleted: int


async def purge_expired_media(session: AsyncSession, *, older_than_days: int | None = None) -> RetentionSummary:
    """Delete media files/rows and captions for finished products older
    than `older_than_days` (defaults to `settings.MEDIA_RETENTION_DAYS`).

    Safe to call every cycle: products with no remaining media are simply
    skipped, so re-running this after a previous purge is a cheap no-op.
    """
    settings = get_settings()
    days = settings.MEDIA_RETENTION_DAYS if older_than_days is None else older_than_days
    if days <= 0:
        return RetentionSummary(0, 0, 0, 0)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await session.execute(
        select(Product)
        .where(Product.status.in_(_TERMINAL_STATUSES))
        .where(Product.discovered_at < cutoff)
        .where(Product.media_items.any())
    )
    products = result.scalars().unique().all()

    products_purged = 0
    media_deleted = 0
    media_failed = 0
    captions_deleted = 0

    for product in products:
        media_result = await session.execute(select(Media).where(Media.product_id == product.id))
        media_items = media_result.scalars().all()
        if not media_items:
            continue

        all_ok = True
        for media in media_items:
            try:
                _delete_media_file(settings, media.file_path)
            except Exception:
                logger.exception(
                    "Failed to delete media file for product_id=%s media_id=%s; leaving the row in place",
                    product.id,
                    media.id,
                )
                media_failed += 1
                all_ok = False
                continue
            media_deleted += 1
            await session.delete(media)

        if all_ok:
            caption_result = await session.execute(select(Caption).where(Caption.product_id == product.id))
            for caption in caption_result.scalars().all():
                await session.delete(caption)
                captions_deleted += 1
            products_purged += 1

        await session.commit()

    return RetentionSummary(
        products_purged=products_purged,
        media_files_deleted=media_deleted,
        media_files_failed=media_failed,
        captions_deleted=captions_deleted,
    )


def _delete_media_file(settings, file_path: str | None) -> None:
    if not file_path:
        return
    if file_path.startswith("https://") or file_path.startswith("http://"):
        key = cloud_storage.object_key_from_public_url(file_path)
        if key is None:
            logger.warning("Media file_path looks remote but isn't a recognized R2 URL, skipping: %s", file_path)
            return
        cloud_storage.delete_file(key)
        return
    # Local storage backend: file_path is relative to MEDIA_STORAGE_DIR.
    root = storage_root(settings.MEDIA_STORAGE_DIR)
    try:
        target = safe_relative_path(root, file_path)
    except ValueError:
        logger.warning("Media file_path escapes storage root, skipping delete: %s", file_path)
        return
    target.unlink(missing_ok=True)
