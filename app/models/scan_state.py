"""
scan_state — one row per channel, tracking scanning progress.

This is what makes the Telegram monitor restart-safe: on every restart the
monitor reads `last_processed_message_id` / `last_processed_message_date`
per channel and resumes from there instead of re-scanning from scratch or
losing its place. It's also what lets `scan_phase` distinguish a channel's
first-ever scan (INITIAL, bounded to the last N days) from steady-state
monitoring (INCREMENTAL) from a resume-after-downtime scan (CATCHUP,
bounded the same way as INITIAL).

The actual "5 days" window is a business rule for the Telegram monitor in
a later phase, not something enforced by this table.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ScanPhase
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.channel import Channel


class ScanState(TimestampMixin, Base):
    __tablename__ = "scan_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    channel_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("channels.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    scan_phase: Mapped[ScanPhase] = mapped_column(
        Enum(ScanPhase, name="scan_phase", native_enum=True),
        nullable=False,
        default=ScanPhase.INITIAL,
        server_default=ScanPhase.INITIAL.value,
    )

    # High-water mark used to resume incremental/catch-up scanning.
    last_processed_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_processed_message_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    initial_scan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    initial_scan_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Used by a later phase to back off / alert on a persistently broken
    # channel (e.g. bot kicked, channel deleted upstream) without a hard
    # failure count sitting only in memory/logs.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    channel: Mapped["Channel"] = relationship(back_populates="scan_state")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ScanState channel_id={self.channel_id} phase={self.scan_phase}>"
