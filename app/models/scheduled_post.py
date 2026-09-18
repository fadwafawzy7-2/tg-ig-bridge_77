"""
scheduled_posts — the publish queue for POST and REEL content only.

Stories are deliberately NOT modeled here — see `story.py`. This table's
strong duplicate-prevention rule ("Posts/Reels need duplicate
prevention") is enforced by a partial unique index in the Phase 2
migration: at most one *active* (PENDING/SCHEDULED/PROCESSING) scheduled
post per (product_id, content_type). A CHECK constraint additionally
restricts `content_type` to POST/REEL at the database level, so this
table can never accidentally hold a STORY row.

`idempotency_key` is what makes the scheduler restart-safe: recomputing
"what should be scheduled" after a crash and re-inserting is a no-op
instead of creating a second queue entry for the same product/slot.
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
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ContentType, PostPublishStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.caption import Caption
    from app.models.product import Product
    from app.models.published_post import PublishedPost


class ScheduledPost(TimestampMixin, Base):
    __tablename__ = "scheduled_posts"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('POST', 'REEL')", name="ck_scheduled_posts_content_type"
        ),
        Index("ix_scheduled_posts_status", "status"),
        Index("ix_scheduled_posts_scheduled_for", "scheduled_for"),
        Index("ix_scheduled_posts_product_id", "product_id"),
        # Strong duplicate prevention for posts/reels: at most one ACTIVE
        # (not yet published/failed/cancelled) scheduled post per product
        # per content type.
        Index(
            "uq_scheduled_posts_active_product_content_type",
            "product_id",
            "content_type",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'SCHEDULED', 'PROCESSING')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type", native_enum=True), nullable=False
    )
    caption_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("captions.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[PostPublishStatus] = mapped_column(
        Enum(PostPublishStatus, name="post_publish_status", native_enum=True),
        nullable=False,
        default=PostPublishStatus.PENDING,
        server_default=PostPublishStatus.PENDING.value,
    )

    # Target publish time. Later phases (Best Time Engine) decide this
    # value; this table just stores and enforces it.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="scheduled_posts")
    caption: Mapped["Caption | None"] = relationship(back_populates="scheduled_posts")
    published_post: Mapped["PublishedPost | None"] = relationship(
        back_populates="scheduled_post", uselist=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<ScheduledPost id={self.id} product_id={self.product_id} "
            f"content_type={self.content_type} status={self.status}>"
        )
