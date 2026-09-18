"""
channels — dynamic registry of monitored Telegram channels.

Channels are never hard-deleted: "removing" a channel means setting
`status = DISABLED` (or `ARCHIVED`). This keeps every `source_messages` /
`products` row that references it valid forever, which is required for
Product -> Source Message traceability and for historical analytics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ChannelStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.scan_state import ScanState
    from app.models.source_message import SourceMessage


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Telegram's own numeric channel id — the real source-of-truth
    # identifier. Never reused/guessable, so it's what we dedupe on.
    telegram_channel_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )

    # Public @handle, if the channel has one. Private channels have none,
    # so this stays nullable and message links can't always be built.
    channel_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Display name at last sync. Telegram channels can rename themselves;
    # this is a live/refreshable field, unlike the immutable snapshot
    # copies stored on `products`.
    channel_title: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[ChannelStatus] = mapped_column(
        Enum(ChannelStatus, name="channel_status", native_enum=True),
        nullable=False,
        default=ChannelStatus.ACTIVE,
        server_default=ChannelStatus.ACTIVE.value,
    )

    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    scan_state: Mapped["ScanState | None"] = relationship(
        back_populates="channel",
        uselist=False,
        cascade="all, delete-orphan",
    )
    source_messages: Mapped[list["SourceMessage"]] = relationship(
        back_populates="channel",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Channel id={self.id} telegram_channel_id={self.telegram_channel_id} "
            f"status={self.status}>"
        )
