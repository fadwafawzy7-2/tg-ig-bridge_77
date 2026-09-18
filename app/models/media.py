"""
media — photos/videos, kept as their own entity separate from Product and
from any publication (scheduled_posts / published_posts / stories).

Media is linked to `source_messages` (where it came from) and, once a
message has been parsed, to `products` (what it's a picture of). The
`product_id` link is nullable specifically so media can be downloaded and
fingerprinted as soon as a message is ingested, before parsing assigns it
to a product — restart-safe, no need to redo the download if parsing
happens later or is retried.

`telegram_file_unique_id`, `perceptual_hash`, and `sha256_hash` are all
indexed so a later-phase duplicate detector can look up "have we seen this
exact file / this visually-similar image before" without scanning the
whole table.

CAVEAT (added in Phase 3): what `telegram_file_id` / `telegram_file_unique_id`
actually contain depends on which Telegram client ingested the row. The
Phase 3 Telethon (MTProto user session) client does NOT produce Bot API
file_id/file_unique_id values — it stores its own MTProto media
identifiers instead, and always leaves `telegram_file_unique_id` NULL (no
reliable stable equivalent exists at the MTProto layer without downloading
the file). See `app/telegram/client.py::_to_message_dto` for exactly what
is stored and why. A future duplicate-detection phase should rely on
`sha256_hash`/`perceptual_hash` (computed after download), not on
`telegram_file_unique_id`, for cross-message fingerprinting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MediaType
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.source_message import SourceMessage
    from app.models.story import Story


class Media(TimestampMixin, Base):
    __tablename__ = "media"
    __table_args__ = (
        UniqueConstraint(
            "source_message_id", "telegram_file_id", name="uq_media_source_message_file"
        ),
        Index("ix_media_source_message_id", "source_message_id"),
        Index("ix_media_telegram_file_unique_id", "telegram_file_unique_id"),
        Index("ix_media_perceptual_hash", "perceptual_hash"),
        Index("ix_media_sha256_hash", "sha256_hash"),
        Index("ix_media_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    source_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_messages.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )

    media_type: Mapped[MediaType] = mapped_column(
        Enum(MediaType, name="media_type", native_enum=True), nullable=False
    )

    # Telegram's file_id (bot-session-scoped, used to re-download) and
    # file_unique_id (stable identifier for the same underlying file,
    # useful for exact-duplicate lookups independent of re-download).
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    file_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Perceptual hash (e.g. pHash) for near-duplicate/visual-similarity
    # detection, and a plain sha256 for byte-exact duplicate detection.
    perceptual_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Order within a Telegram album (media_group_id) or within a product's
    # gallery.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source_message: Mapped["SourceMessage"] = relationship(back_populates="media_items")
    product: Mapped["Product | None"] = relationship(back_populates="media_items")
    stories: Mapped[list["Story"]] = relationship(back_populates="media")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Media id={self.id} type={self.media_type} product_id={self.product_id}>"
