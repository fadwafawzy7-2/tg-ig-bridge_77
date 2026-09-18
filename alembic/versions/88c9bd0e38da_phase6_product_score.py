"""phase6: products.score for the scoring engine

Adds a single nullable `products.score` column (0-100), plus a CHECK
constraint enforcing that range at the database level.

Why this was necessary: Phase 6 requires a scoring engine whose output
drives queue ORDERING (score DESC) and a `reprioritize` domain operation
that manually overrides a product's priority. Both need the value
persisted and queryable/sortable in SQL — computing it on the fly at read
time would make `reprioritize` (an explicit override) impossible to
express, and would make "ORDER BY score" require recomputing a score for
every row on every queue read instead of a plain indexed sort.

Nothing else needed a schema change for Phase 6: `scheduled_posts`
already has `idempotency_key` (unique) and a partial unique index
enforcing at most one ACTIVE (PENDING/SCHEDULED/PROCESSING) row per
(product_id, content_type) — exactly the DB-level duplicate-prevention
Phase 6 needs for the scheduler, added back in Phase 2. `daily_limits`
already models a per-day, per-content-type MAXIMUM with a running
published count, also from Phase 2. `products.status` already has
VALIDATED/ELIGIBLE/QUEUED/SCHEDULED/PUBLISHED/SKIPPED/DUPLICATE/REJECTED/
FAILED covering the full queue lifecycle. None of that needed touching.

Revision ID: 88c9bd0e38da
Revises: 9c3f2a7d1b44
Create Date: 2026-09-08 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "88c9bd0e38da"
down_revision: Union[str, None] = "9c3f2a7d1b44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("score", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_products_score_range",
        "products",
        "score IS NULL OR (score >= 0 AND score <= 100)",
    )
    op.create_index("ix_products_score", "products", ["score"])


def downgrade() -> None:
    op.drop_index("ix_products_score", table_name="products")
    op.drop_constraint("ck_products_score_range", "products", type_="check")
    op.drop_column("products", "score")
