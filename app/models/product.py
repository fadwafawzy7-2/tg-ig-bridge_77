"""
products — the canonical product entity, decoupled from raw messages.

Traceability to Telegram (the source of truth) is kept two ways on
purpose:

1. `primary_source_message_id` — a real foreign key to the exact
   `source_messages` row this product was first discovered/parsed from.
   This is what guarantees "every Product is traceable to a specific
   Source Message" at the database (referential-integrity) level.
2. `source_channel_id` / `source_channel_name` / `source_message_id` /
   `source_message_date` / `source_message_link` — plain snapshot columns,
   stored explicitly (not just derived via a join) so the discovery
   context survives even if the channel is later renamed, and so it can be
   read without a join in hot paths (dashboards, logs, error records).

A product can legitimately come from more than one Telegram message (the
same item reposted, or reappearing in another channel). Rather than
overwriting `primary_source_message_id`, additional occurrences are
recorded in `product_source_messages` — see that model's docstring.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ContentType, ProductStatus
from app.models.mixins import TimestampMixin
from app.products_codes import generate_product_code

if TYPE_CHECKING:
    from app.models.caption import Caption
    from app.models.channel import Channel
    from app.models.error_log import ErrorLog
    from app.models.media import Media
    from app.models.product_source_message import ProductSourceMessage
    from app.models.published_post import PublishedPost
    from app.models.scheduled_post import ScheduledPost
    from app.models.source_message import SourceMessage
    from app.models.story import Story


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_status", "status"),
        Index("ix_products_content_hash", "content_hash"),
        Index("ix_products_primary_source_message_id", "primary_source_message_id"),
        Index("ix_products_channel_id", "channel_id"),
        Index("ix_products_is_duplicate_of", "is_duplicate_of"),
        Index("ix_products_score", "score"),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)", name="ck_products_score_range"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Stable customer-facing identifier. Deliberately letters-only after PRD-
    # so it can safely be appended to validated captions without looking like a price.
    product_code: Mapped[str] = mapped_column(String(12), nullable=False, unique=True, index=True, default=generate_product_code)

    primary_source_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_messages.id", ondelete="RESTRICT"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False
    )

    # --- Explicit source snapshot (see module docstring) ---
    source_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_channel_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_message_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, name="product_status", native_enum=True),
        nullable=False,
        default=ProductStatus.DISCOVERED,
        server_default=ProductStatus.DISCOVERED.value,
    )

    # Set only by the dashboard bot's manual-capture flow (the person picks
    # POST/REEL/STORY right after forwarding an item). When set, the
    # scheduler publishes this product as exactly that content type
    # instead of guessing one from its media (see app/queue/scheduler.py).
    # Left null for anything discovered by other means.
    preferred_content_type: Mapped["ContentType | None"] = mapped_column(
        Enum(ContentType, name="content_type", native_enum=True),
        nullable=True,
    )

    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Normalized-content fingerprint (e.g. hash of cleaned title+description
    # +price) used by later-phase duplicate detection to find candidate
    # matches quickly. Deliberately just an indexed column, not a unique
    # constraint — duplicates are expected to exist as rows (status=
    # DUPLICATE), not be rejected at insert time.
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # --- Phase 6: scoring engine output (0-100), drives queue ordering ---
    # Nullable: unset until `app.queue.eligibility_service` computes it
    # (typically once a product reaches VALIDATED). Overwritable by the
    # `reprioritize` domain operation as a manual priority override — see
    # `app/queue/operations.py`. See the Phase 6 migration for why this
    # needed a schema change when nothing else in Phase 6 did.
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_duplicate_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )

    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    channel: Mapped["Channel"] = relationship()
    primary_source_message: Mapped["SourceMessage"] = relationship(
        back_populates="primary_products",
        foreign_keys="Product.primary_source_message_id",
    )
    duplicate_of: Mapped["Product | None"] = relationship(
        remote_side="Product.id", foreign_keys="Product.is_duplicate_of"
    )
    source_message_links: Mapped[list["ProductSourceMessage"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    media_items: Mapped[list["Media"]] = relationship(back_populates="product")
    captions: Mapped[list["Caption"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    scheduled_posts: Mapped[list["ScheduledPost"]] = relationship(back_populates="product")
    published_posts: Mapped[list["PublishedPost"]] = relationship(back_populates="product")
    stories: Mapped[list["Story"]] = relationship(back_populates="product")
    errors: Mapped[list["ErrorLog"]] = relationship(back_populates="product")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Product id={self.id} status={self.status} title={self.title!r}>"
