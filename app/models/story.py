"""
stories — its own table, separate from scheduled_posts/published_posts,
because stories have different business rules: repeats are explicitly
allowed ("Stories تسمح بالتكرار"), so there is NO uniqueness/duplicate-
prevention constraint tying a story to a product the way `scheduled_posts`
has for posts/reels. A single row tracks a story's full lifecycle
(pending -> scheduled -> published -> expired) rather than splitting
queue/ledger into two tables, since stories are simpler and short-lived.

`expires_at` supports cleanup/analytics once Instagram's ~24h story
expiry is reached; it's set by application code when the story is
published, not enforced here.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import StoryStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.analytics import Analytics
    from app.models.media import Media
    from app.models.product import Product


class Story(TimestampMixin, Base):
    __tablename__ = "stories"
    __table_args__ = (
        Index("ix_stories_status", "status"),
        Index("ix_stories_product_id", "product_id"),
        Index("ix_stories_scheduled_for", "scheduled_for"),
        Index("ix_stories_published_at", "published_at"),
        Index(
            "uq_stories_instagram_story_id",
            "instagram_story_id",
            unique=True,
            postgresql_where=text("instagram_story_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    media_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("media.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[StoryStatus] = mapped_column(
        Enum(StoryStatus, name="story_status", native_enum=True),
        nullable=False,
        default=StoryStatus.PENDING,
        server_default=StoryStatus.PENDING.value,
    )

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    instagram_story_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="stories")
    media: Mapped["Media | None"] = relationship(back_populates="stories")
    analytics: Mapped[list["Analytics"]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Story id={self.id} product_id={self.product_id} status={self.status}>"
