"""
daily_limits — separate MAXIMUM allowed count per day, per content type
(POST / REEL / STORY), plus a running count of how many have actually
been published that day.

One row per (date, content_type), created/upserted by the scheduler in a
later phase. `max_allowed` is explicitly a ceiling ("MAXIMUM وليس
minimum") — the scheduler must stop queueing that content type for that
day once `published_count >= max_allowed`, never treat it as a quota to
fill.
"""

from __future__ import annotations

from datetime import date as date_

from sqlalchemy import BigInteger, Date, Enum, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ContentType
from app.models.mixins import TimestampMixin


class DailyLimit(TimestampMixin, Base):
    __tablename__ = "daily_limits"
    __table_args__ = (
        UniqueConstraint("date", "content_type", name="uq_daily_limits_date_content_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    date: Mapped[date_] = mapped_column(Date, nullable=False)
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type", native_enum=True), nullable=False
    )

    max_allowed: Mapped[int] = mapped_column(Integer, nullable=False)
    published_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<DailyLimit date={self.date} content_type={self.content_type} "
            f"{self.published_count}/{self.max_allowed}>"
        )
