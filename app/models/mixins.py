"""
Shared model mixins.

Every domain table gets `created_at` / `updated_at` the same way, via
Postgres-side defaults (`server_default=func.now()`), so timestamps are
correct even if the application server's clock is wrong or a row is
inserted directly in the database (e.g. by a migration data fix).
"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Adds `created_at` and `updated_at` columns to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
