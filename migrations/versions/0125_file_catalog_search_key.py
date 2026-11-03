"""Add a locale-independent search key to the private metadata catalog.

Revision ID: 0125
Revises: 0124

Run with catalog writers stopped; old application versions cannot insert after
this migration. Backfill is bounded in memory, not a provider or content read.
"""

import unicodedata

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def _search_key(value):
    # Frozen to match FileCatalogManager's NFKC/casefold algorithm at revision.
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", value).casefold())


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("0125 requires an online connection to backfill search keys")
    schema = settings.DB_SCHEMA
    op.add_column("file_catalog_objects", sa.Column("name_search", sa.Text(), nullable=True), schema=schema)
    table = sa.table(
        "file_catalog_objects",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("name_search", sa.Text()),
        schema=schema,
    )
    connection = op.get_bind()
    cursor = 0
    while True:
        rows = connection.execute(
            sa.select(table.c.id, table.c.name).where(table.c.id > cursor).order_by(table.c.id).limit(500)
        ).all()
        if not rows:
            break
        connection.execute(
            table.update().where(table.c.id == sa.bindparam("object_id")).values(name_search=sa.bindparam("folded")),
            [{"object_id": row.id, "folded": _search_key(row.name)} for row in rows],
        )
        cursor = rows[-1].id
    op.alter_column("file_catalog_objects", "name_search", existing_type=sa.Text(), nullable=False, schema=schema)


def downgrade():
    op.drop_column("file_catalog_objects", "name_search", schema=settings.DB_SCHEMA)
