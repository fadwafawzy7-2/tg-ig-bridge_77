"""
captions — AI-generated caption text for a product, versioned.

Multiple captions can be generated per product (retries, alternate
phrasing); `is_selected` marks the one to actually publish with. A partial
unique index (see the Phase 2 migration) enforces at most one selected
caption per product directly in the database, not just in application
code.

SCHEMA CHANGE (Phase 5): `validation_status` / `rejection_reason` were
added by the `..._phase5_caption_validation` migration. Reason this was
judged necessary (Phase 5 requirement: "never allow publish before a
caption is validated against the source, with a clear rejection reason"):

- Multiple candidate versions are generated and stored per product (this
  was already true in Phase 2's design), so validation outcome has to be
  tracked PER VERSION, not per product — `products.rejection_reason`
  (Phase 2) is a single field on the product and cannot distinguish "why
  was version 2 rejected" from "why was version 3 rejected".
- `is_selected` alone cannot express *why* the other versions were not
  selected — that distinction (rejected-for-a-reason vs. simply
  not-picked-among-valid-alternatives) matters for audit and for
  reviewing/improving prompts later.
- A DB-level CHECK constraint (mirroring the existing partial-unique-index
  pattern already used for `is_selected` in this table) can then guarantee
  — independent of any application bug — that a caption can never be
  marked selected/publishable unless it actually passed validation.

Both columns are nullable/defaulted so this is additive only; no existing
Phase 2/3/4 row or query is affected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Enum, ForeignKey, Index, Integer
from sqlalchemy import String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CaptionValidationStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.published_post import PublishedPost
    from app.models.scheduled_post import ScheduledPost


class Caption(TimestampMixin, Base):
    __tablename__ = "captions"
    __table_args__ = (
        Index("ix_captions_product_id", "product_id"),
        # At most one selected caption per product.
        Index(
            "uq_captions_one_selected_per_product",
            "product_id",
            unique=True,
            postgresql_where=sql_text("is_selected = true"),
        ),
        # A caption can only be the publish-ready one if it actually
        # passed the Phase 5 source-fact validation gate. DB-enforced so
        # this can never be violated by an application-level bug.
        CheckConstraint(
            "is_selected = false OR validation_status = 'PASSED'",
            name="ck_captions_selected_implies_passed",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )

    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Name of the model/engine that generated this caption (e.g. a model
    # name), for audit purposes only — never a credential.
    generated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Phase 5: source-fact validation outcome, per version ---
    validation_status: Mapped[CaptionValidationStatus] = mapped_column(
        Enum(CaptionValidationStatus, name="caption_validation_status", native_enum=True),
        nullable=False,
        default=CaptionValidationStatus.PENDING,
        server_default=CaptionValidationStatus.PENDING.value,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped["Product"] = relationship(back_populates="captions")
    scheduled_posts: Mapped[list["ScheduledPost"]] = relationship(back_populates="caption")
    published_posts: Mapped[list["PublishedPost"]] = relationship(back_populates="caption")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Caption id={self.id} product_id={self.product_id} version={self.version} "
            f"validation_status={self.validation_status}>"
        )
