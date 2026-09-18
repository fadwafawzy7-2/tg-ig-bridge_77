"""
source_messages — raw record of every relevant Telegram message ingested.

This is the literal "Telegram is source of truth" table: nothing is
invented here, it's a durable copy of what Telegram sent. Every Product is
ultimately traceable back to (at least) one row here.

`unique(channel_id, telegram_message_id)` is what makes ingestion
idempotent: re-scanning a message the monitor already saw (e.g. after a
restart, or during a catch-up scan that overlaps the last incremental run)
is a no-op instead of creating duplicate rows.
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import SourceMessageStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.channel import Channel
    from app.models.media import Media
    from app.models.product import Product
    from app.models.product_source_message import ProductSourceMessage


class SourceMessage(TimestampMixin, Base):
    __tablename__ = "source_messages"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "telegram_message_id", name="uq_source_messages_channel_message"
        ),
        Index("ix_source_messages_channel_id", "channel_id"),
        Index("ix_source_messages_message_date", "message_date"),
        Index("ix_source_messages_media_group_id", "media_group_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False
    )

    # Telegram's own message id, unique only within its channel (hence the
    # composite unique constraint above, not a standalone unique column).
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Only buildable for channels with a public @username; null otherwise.
    message_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    has_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Telegram groups an album's messages (multiple photos/videos posted
    # together) under one media_group_id. Needed to correlate multiple
    # media items back to a single logical post/product.
    media_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[SourceMessageStatus] = mapped_column(
        Enum(SourceMessageStatus, name="source_message_status", native_enum=True),
        nullable=False,
        default=SourceMessageStatus.PENDING,
        server_default=SourceMessageStatus.PENDING.value,
    )

    channel: Mapped["Channel"] = relationship(back_populates="source_messages")
    media_items: Mapped[list["Media"]] = relationship(back_populates="source_message")
    primary_products: Mapped[list["Product"]] = relationship(
        back_populates="primary_source_message",
        foreign_keys="Product.primary_source_message_id",
    )
    product_links: Mapped[list["ProductSourceMessage"]] = relationship(
        back_populates="source_message"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<SourceMessage id={self.id} channel_id={self.channel_id} "
            f"telegram_message_id={self.telegram_message_id}>"
        )
