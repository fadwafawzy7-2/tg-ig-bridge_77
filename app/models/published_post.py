"""
published_posts — permanent audit ledger of POST/REEL content actually
published to Instagram.

This table is intentionally append-mostly: `scheduled_post_id` is
SET NULL (not CASCADE) if the originating queue row is ever cleaned up, so
publish history/analytics never lose their anchor row. `content_type` is
restricted to POST/REEL at the DB level, matching `scheduled_posts`
(stories have their own table, see `story.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ContentType, PublishedContentStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.analytics import Analytics
    from app.models.caption import Caption
    from app.models.product import Product
    from app.models.scheduled_post import ScheduledPost


class PublishedPost(TimestampMixin, Base):
    __tablename__ = "published_posts"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('POST', 'REEL')", name="ck_published_posts_content_type"
        ),
        Index("ix_published_posts_product_id", "product_id"),
        Index("ix_published_posts_published_at", "published_at"),
        Index("ix_published_posts_content_type", "content_type"),
        Index(
            "uq_published_posts_instagram_media_id",
            "instagram_media_id",
            unique=True,
            postgresql_where=text("instagram_media_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    scheduled_post_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("scheduled_posts.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    caption_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("captions.id", ondelete="SET NULL"), nullable=True
    )

    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type", native_enum=True), nullable=False
    )

    # Instagram's own id for the published media, once known. Used to
    # correlate with analytics pulls and to detect if we've already
    # published a given remote object (idempotency for the publish step).
    instagram_media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[PublishedContentStatus] = mapped_column(
        Enum(PublishedContentStatus, name="published_content_status", native_enum=True),
        nullable=False,
        default=PublishedContentStatus.LIVE,
        server_default=PublishedContentStatus.LIVE.value,
    )

    scheduled_post: Mapped["ScheduledPost | None"] = relationship(
        back_populates="published_post"
    )
    product: Mapped["Product"] = relationship(back_populates="published_posts")
    caption: Mapped["Caption | None"] = relationship(back_populates="published_posts")
    analytics: Mapped[list["Analytics"]] = relationship(
        back_populates="published_post", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<PublishedPost id={self.id} product_id={self.product_id} "
            f"content_type={self.content_type} status={self.status}>"
        )
