"""
product_source_messages — extra Telegram messages a product also appeared
in, beyond its primary discovery message.

`products.primary_source_message_id` records where a product was *first*
discovered. When the same product later shows up again (reposted in the
same channel, mirrored into another monitored channel, etc.), duplicate
detection in a later phase links the new `source_messages` row to the
*existing* `Product` here instead of creating a second product — this is
the mechanism that lets "the same product can appear in more than one
Telegram message" coexist with "every product is traceable to a specific
source message" without contradiction.

`unique(product_id, source_message_id)` keeps re-linking idempotent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.source_message import SourceMessage


class ProductSourceMessage(TimestampMixin, Base):
    __tablename__ = "product_source_messages"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "source_message_id", name="uq_product_source_messages_pair"
        ),
        Index("ix_product_source_messages_source_message_id", "source_message_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_messages.id", ondelete="CASCADE"), nullable=False
    )

    product: Mapped["Product"] = relationship(back_populates="source_message_links")
    source_message: Mapped["SourceMessage"] = relationship(back_populates="product_links")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<ProductSourceMessage product_id={self.product_id} "
            f"source_message_id={self.source_message_id}>"
        )
