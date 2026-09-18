"""
errors — persistent, system-wide error/event log across all subsystems
(Telegram monitor, AI captioning, Instagram publisher, scheduler, ...).

This is separate from the Foundation's in-request `AppError` handling
(`app/core/exceptions.py`), which only covers HTTP request/response
errors. Background/pipeline failures (a channel scan failing, AI
captioning failing, a scheduled publish failing) have no HTTP request to
attach to, so they're persisted here instead — restart-safe visibility
into what went wrong, for a later dashboard/alerting phase.

`error_code` is free-text on purpose so it can reuse the same short codes
as `AppError.error_code` where relevant, without a hard dependency between
the two systems.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ErrorSeverity
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.channel import Channel
    from app.models.product import Product
    from app.models.source_message import SourceMessage


class ErrorLog(TimestampMixin, Base):
    __tablename__ = "errors"
    __table_args__ = (
        Index("ix_errors_source", "source"),
        Index("ix_errors_severity", "severity"),
        Index("ix_errors_resolved", "resolved"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Which subsystem raised it, e.g. "telegram_monitor", "ai_captioning",
    # "instagram_publisher", "scheduler".
    source: Mapped[str] = mapped_column(String(100), nullable=False)

    severity: Mapped[ErrorSeverity] = mapped_column(
        Enum(ErrorSeverity, name="error_severity", native_enum=True),
        nullable=False,
        default=ErrorSeverity.ERROR,
        server_default=ErrorSeverity.ERROR.value,
    )

    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional links to the entity involved, kept nullable and SET NULL on
    # delete so the log entry survives even if the referenced row is later
    # removed — it's a historical record, not a live reference.
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    source_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_messages.id", ondelete="SET NULL"), nullable=True
    )

    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    product: Mapped["Product | None"] = relationship(back_populates="errors")
    channel: Mapped["Channel | None"] = relationship()
    source_message: Mapped["SourceMessage | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ErrorLog id={self.id} source={self.source} severity={self.severity}>"
