"""
Turns a `source_messages` row into a `products` row (or links it to an
existing one), using ONLY `text_parser.py` (deterministic, no AI).

Duplicate prevention: before ever creating a `Product`, this module
checks for an existing product with the same `content_hash`. If one
exists, the incoming source message is linked to it via
`product_source_messages` (idempotent — unique constraint already
prevents duplicate links) and NO new product row is created. This is
stronger than "create then flag as duplicate": a genuine duplicate never
gets its own product row in the first place.

Source traceability (Phase 2/3 requirement) is preserved on every newly
created product: `primary_source_message_id` (a real FK) plus the
explicit `source_channel_id` / `source_channel_name` / `source_message_id`
/ `source_message_date` / `source_message_link` snapshot columns are all
populated from the channel + source message being parsed.

Existing product rows are NEVER updated by this module — once a product
exists for a given `content_hash`, later messages that match it only add
a link, never touch the product's own fields. This mirrors Phase 3's
"never overwrite source data" rule at the product level too.

One message's parsing failure is isolated (logged to `errors`, message
marked FAILED) and never blocks the rest of a batch — same pattern as
`app.telegram.scan_service`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.models.enums import ErrorSeverity, ProductStatus, SourceMessageStatus
from app.models.error_log import ErrorLog
from app.models.product import Product
from app.products_codes import generate_product_code
from app.models.product_source_message import ProductSourceMessage
from app.models.media import Media
from app.models.source_message import SourceMessage
from app.parsing.text_parser import parse_product_fields

logger = logging.getLogger(__name__)

ERROR_SOURCE = "product_parser"


@dataclass(frozen=True)
class ProcessResult:
    source_message_id: int
    outcome: str  # "created" | "duplicate_linked" | "ignored" | "failed"
    product_id: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def _find_existing_product_by_hash(
    session: AsyncSession, content_hash: str
) -> Product | None:
    result = await session.execute(select(Product).where(Product.content_hash == content_hash))
    return result.scalars().first()


async def _link_to_existing_product(
    session: AsyncSession, product: Product, source_message: SourceMessage
) -> None:
    """Record that `source_message` is another occurrence of `product`.

    Idempotent via ON CONFLICT DO NOTHING on the (product_id,
    source_message_id) unique constraint — re-processing the same
    message never creates a duplicate link.
    """
    stmt = pg_insert(ProductSourceMessage).values(
        product_id=product.id, source_message_id=source_message.id
    ).on_conflict_do_nothing(
        index_elements=["product_id", "source_message_id"]
    )
    await session.execute(stmt)


async def _link_source_media_to_product(
    session: AsyncSession, product_id: int, source_message: SourceMessage
) -> None:
    """Attach all media belonging to the source message or its Telegram album.

    Telegram albums are represented as multiple source_message rows sharing
    media_group_id. The product text may exist on only one message, so every
    media row in the same channel+album must follow that product. No visual
    inference is performed; this is purely source-message linkage.
    """
    if source_message.media_group_id is not None:
        group_ids = (await session.execute(
            select(SourceMessage.id).where(
                SourceMessage.channel_id == source_message.channel_id,
                SourceMessage.media_group_id == source_message.media_group_id,
            )
        )).scalars().all()
    else:
        group_ids = [source_message.id]
    if group_ids:
        await session.execute(
            update(Media).where(Media.source_message_id.in_(group_ids)).values(product_id=product_id)
        )


async def process_source_message(
    session: AsyncSession, source_message: SourceMessage
) -> ProcessResult:
    try:
        parsed = parse_product_fields(source_message.raw_text)

        if parsed is None:
            source_message.status = SourceMessageStatus.IGNORED
            await session.commit()
            return ProcessResult(source_message_id=source_message.id, outcome="ignored")

        existing_product = await _find_existing_product_by_hash(session, parsed.content_hash)

        if existing_product is not None:
            await _link_to_existing_product(session, existing_product, source_message)
            await _link_source_media_to_product(session, existing_product.id, source_message)
            source_message.status = SourceMessageStatus.PROCESSED
            await session.commit()
            return ProcessResult(
                source_message_id=source_message.id,
                outcome="duplicate_linked",
                product_id=existing_product.id,
            )

        channel = await session.get(Channel, source_message.channel_id)
        if channel is None:
            raise RuntimeError(
                f"source_message {source_message.id} references missing "
                f"channel_id={source_message.channel_id}"
            )

        product = Product(
            product_code=generate_product_code(),
            primary_source_message_id=source_message.id,
            channel_id=channel.id,
            source_channel_id=channel.telegram_channel_id,
            source_channel_name=channel.channel_title,
            source_message_id=source_message.telegram_message_id,
            source_message_date=source_message.message_date,
            source_message_link=source_message.message_link,
            status=ProductStatus.PARSED,
            title=parsed.title,
            description=parsed.description,
            price=parsed.price,
            currency=parsed.currency,
            content_hash=parsed.content_hash,
        )
        session.add(product)
        await session.flush()
        await _link_source_media_to_product(session, product.id, source_message)
        source_message.status = SourceMessageStatus.PROCESSED
        await session.commit()
        await session.refresh(product)

        return ProcessResult(
            source_message_id=source_message.id, outcome="created", product_id=product.id
        )

    except Exception as exc:  # noqa: BLE001 - one bad message must never
        # block the rest of a batch; see module docstring.
        logger.exception("Parsing failed for source_message_id=%s", source_message.id)
        await session.rollback()
        source_message.status = SourceMessageStatus.FAILED
        session.add(
            ErrorLog(
                source=ERROR_SOURCE,
                severity=ErrorSeverity.ERROR,
                message=f"Parsing failed for source_message {source_message.id}: {exc}",
                source_message_id=source_message.id,
                channel_id=source_message.channel_id,
            )
        )
        await session.commit()
        return ProcessResult(source_message_id=source_message.id, outcome="failed", error=str(exc))
