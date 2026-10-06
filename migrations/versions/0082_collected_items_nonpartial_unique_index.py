"""replace partial unique index on collected_items with a regular one

PostgreSQL can only use a *non-partial* unique index as an ON CONFLICT DO
NOTHING target. The partial index ``uq_collected_items_source_external``
(``WHERE external_id IS NOT NULL``) existed so that multiple NULL
external_ids per source would be legal. A regular UNIQUE index provides
the same NULL-handling (PostgreSQL treats NULLs as distinct in a UNIQUE
constraint), so dropping the WHERE clause changes no runtime behaviour —
it only makes the index usable by the ``ON CONFLICT`` clause in
``CollectedItemManager.store_items``.

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-06

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.config import settings

revision: str = "0082"
down_revision: Union[str, None] = "0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "collected_items"
INDEX_NAME = "uq_collected_items_source_external"


def upgrade() -> None:
    op.drop_index(INDEX_NAME, table_name=TABLE, schema=settings.DB_SCHEMA)
    op.create_index(
        INDEX_NAME,
        TABLE,
        ["source_id", "external_id"],
        unique=True,
        schema=settings.DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name=TABLE, schema=settings.DB_SCHEMA)
    op.create_index(
        INDEX_NAME,
        TABLE,
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
        schema=settings.DB_SCHEMA,
    )
