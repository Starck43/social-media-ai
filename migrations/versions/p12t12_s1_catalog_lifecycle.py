"""P12-T12 S1: Catalog lifecycle honesty for completed scans.

Revision ID: p12t12_s1_lifecycle
Revises: p8t51_job_type_width
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p12t12_s1_lifecycle"
down_revision = "p8t51_job_type_width"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.add_column(
        "file_catalog_objects",
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="present"),
        schema=schema,
    )
    op.add_column(
        "file_catalog_objects",
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "file_catalog_objects",
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "file_catalog_objects",
        sa.Column("last_scan_token", sa.String(32), nullable=True),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_file_catalog_lifecycle_state",
        "file_catalog_objects",
        "lifecycle_state IN ('present','missing','stale')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_file_catalog_lifecycle_missing_since",
        "file_catalog_objects",
        "(lifecycle_state = 'present' AND missing_since IS NULL) "
        "OR (lifecycle_state IN ('missing','stale') AND missing_since IS NOT NULL)",
        schema=schema,
    )


def downgrade():
    schema = settings.DB_SCHEMA
    op.drop_constraint("ck_file_catalog_lifecycle_missing_since", "file_catalog_objects", schema=schema, type_="check")
    op.drop_constraint("ck_file_catalog_lifecycle_state", "file_catalog_objects", schema=schema, type_="check")
    op.drop_column("file_catalog_objects", "last_scan_token", schema=schema)
    op.drop_column("file_catalog_objects", "missing_since", schema=schema)
    op.drop_column("file_catalog_objects", "reconciled_at", schema=schema)
    op.drop_column("file_catalog_objects", "lifecycle_state", schema=schema)
