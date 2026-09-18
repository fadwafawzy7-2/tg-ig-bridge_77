"""add products.preferred_content_type for manual-capture flow

Revision ID: c1a2b3d4e5f6
Revises: b7c4d1e2f901
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, None] = "b7c4d1e2f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # content_type ENUM already exists (created for daily_limits /
    # scheduled_posts / published_posts in the phase-2 migration) —
    # create_type=False reuses it instead of trying to redefine it.
    content_type = postgresql.ENUM("POST", "REEL", "STORY", name="content_type", create_type=False)
    op.add_column(
        "products",
        sa.Column("preferred_content_type", content_type, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "preferred_content_type")
