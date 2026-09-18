"""phase5: per-caption-version validation status + rejection reason

Adds `captions.validation_status` and `captions.rejection_reason`, plus a
CHECK constraint that a caption can only be `is_selected=True` if it has
`validation_status='PASSED'`.

Why this was necessary (see docstring in app/models/caption.py for the
full reasoning): Phase 5 requires a hard gate — no caption may be
publish-ready unless it has been validated against
`source_messages.raw_text` and found to contain no unsupported claims and
no merchant price. Multiple candidate caption *versions* are generated and
stored per product, so that outcome (and, on rejection, the reason) has to
live on each caption row, not on the product. This is additive/nullable-
safe and does not touch any Phase 2/3/4 table or data.

Revision ID: 9c3f2a7d1b44
Revises: 1e420e94f57e
Create Date: 2026-09-08 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9c3f2a7d1b44"
down_revision: Union[str, None] = "1e420e94f57e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

caption_validation_status = postgresql.ENUM(
    "PENDING", "PASSED", "REJECTED", name="caption_validation_status"
)


def upgrade() -> None:
    caption_validation_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "captions",
        sa.Column(
            "validation_status",
            postgresql.ENUM(
                "PENDING", "PASSED", "REJECTED",
                name="caption_validation_status",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "captions",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_captions_selected_implies_passed",
        "captions",
        "is_selected = false OR validation_status = 'PASSED'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_captions_selected_implies_passed", "captions", type_="check")
    op.drop_column("captions", "rejection_reason")
    op.drop_column("captions", "validation_status")
    caption_validation_status.drop(op.get_bind(), checkfirst=True)
