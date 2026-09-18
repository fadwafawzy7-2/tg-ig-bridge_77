"""
analytics — engagement metrics snapshots for published posts/reels and
stories, feeding a later-phase Best Time Engine and reporting.

Exactly one of `published_post_id` / `story_id` must be set (enforced by a
CHECK constraint in the migration) — a single table covers both rather
than splitting into two near-identical tables, since the metrics
themselves are the same shape either way.

Metrics are snapshotted per `metric_date` (not overwritten in place) so
growth over time is preserved; a partial unique index per parent+date
keeps re-fetching the same day's numbers idempotent (upsert, not
duplicate rows).
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.published_post import PublishedPost
    from app.models.story import Story


class Analytics(TimestampMixin, Base):
    __tablename__ = "analytics"
    __table_args__ = (
        CheckConstraint(
            "(published_post_id IS NOT NULL)::int + (story_id IS NOT NULL)::int = 1",
            name="ck_analytics_exactly_one_parent",
        ),
        Index("ix_analytics_metric_date", "metric_date"),
        Index(
            "uq_analytics_published_post_metric_date",
            "published_post_id",
            "metric_date",
            unique=True,
            postgresql_where=text("published_post_id IS NOT NULL"),
        ),
        Index(
            "uq_analytics_story_metric_date",
            "story_id",
            "metric_date",
            unique=True,
            postgresql_where=text("story_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    published_post_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("published_posts.id", ondelete="CASCADE"), nullable=True
    )
    story_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("stories.id", ondelete="CASCADE"), nullable=True
    )

    metric_date: Mapped[date_] = mapped_column(Date, nullable=False)

    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    published_post: Mapped["PublishedPost | None"] = relationship(back_populates="analytics")
    story: Mapped["Story | None"] = relationship(back_populates="analytics")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Analytics id={self.id} metric_date={self.metric_date}>"
