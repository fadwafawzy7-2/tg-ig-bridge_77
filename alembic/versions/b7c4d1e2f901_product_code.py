"""add stable customer-facing product code

Revision ID: b7c4d1e2f901
Revises: 88c9bd0e38da
"""
from typing import Sequence, Union
import string
from alembic import op
import sqlalchemy as sa

revision: str = "b7c4d1e2f901"
down_revision: Union[str, None] = "88c9bd0e38da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _letters(n: int, width: int = 8) -> str:
    alphabet = string.ascii_uppercase
    out = []
    for _ in range(width):
        out.append(alphabet[n % 26])
        n //= 26
    return "".join(reversed(out))


def upgrade() -> None:
    op.add_column("products", sa.Column("product_code", sa.String(length=12), nullable=True))
    bind = op.get_bind()
    rows = list(bind.execute(sa.text("SELECT id FROM products ORDER BY id")).scalars())
    for idx, product_id in enumerate(rows, start=1):
        code = "PRD-" + _letters(idx)
        bind.execute(
            sa.text("UPDATE products SET product_code = :code WHERE id = :id"),
            {"code": code, "id": product_id},
        )
    op.alter_column("products", "product_code", nullable=False)
    op.create_index("ix_products_product_code", "products", ["product_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_products_product_code", table_name="products")
    op.drop_column("products", "product_code")
