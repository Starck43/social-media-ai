"""Add opt-in bounded recursive traversal to private scan checkpoints.

Revision ID: 0127
Revises: 0126
Existing checkpoints retain single-folder semantics; no provider is activated.
Stop catalog writers and deploy matching code together before using traversal.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.add_column("file_catalog_scans", sa.Column("traversal", postgresql.JSONB(), nullable=True), schema=schema)
    op.create_check_constraint(
        "ck_file_scan_traversal",
        "file_catalog_scans",
        "traversal IS NULL OR (jsonb_typeof(traversal) = 'object' AND octet_length(traversal::text) <= 2200000)",
        schema=schema,
    )


def downgrade():
    schema = settings.DB_SCHEMA
    # A recursive cursor must never be reinterpreted as a one-folder checkpoint.
    op.execute(sa.text(f'DELETE FROM "{schema}".file_catalog_scans WHERE traversal IS NOT NULL'))
    op.drop_constraint("ck_file_scan_traversal", "file_catalog_scans", schema=schema, type_="check")
    op.drop_column("file_catalog_scans", "traversal", schema=schema)
