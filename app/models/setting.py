"""
settings — small generic key/value table for admin-configurable app
settings (e.g. future Best Time Engine parameters, feature toggles).

Deliberately plain `Text` for `value`, not JSON/JSONB — this project's
domain data (products, media, posts, ...) is fully modeled as real
relational tables per Phase 2 instructions, and this table stays that way
too rather than becoming a place to stash structured data. The
application layer is responsible for parsing/casting a setting's value to
whatever type it needs.

NEVER store secrets or tokens here — this table lives in the same
database as everything else and has no special access control beyond the
DB connection itself; credentials belong in environment variables
(`app/core/config.py`), per the Foundation's existing convention.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Setting key={self.key!r}>"
