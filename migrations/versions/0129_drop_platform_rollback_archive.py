"""0118: remove the inert 0120 rollback archive from the platform catalog.

0120 copied the entire pre-migration `platforms` and `sources` snapshot into
`params._migration_0120` of every canonical row. It is a downgrade input, stored
once per row and then carried by every platform edit: in the live catalog it is
~2.8 KB of `sources` rows per platform, inside the field the admin edits by hand,
and it duplicates the same snapshot across rows. Downgrading across 0120 is
already refused once that copy no longer matches
(platform_rollback_archive_changed), and restoring a pre-0120 catalog from a
platform row is not a supported rollback.

Destructive and irreversible by design: the archive is deleted, and the
downgrade refuses with a safe message. Take a `pg_dump` before applying if a
0120 rollback is still wanted.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None

ARCHIVE = "_migration_0120"


def _without_archive(value) -> dict | None:
    if not isinstance(value, dict) or ARCHIVE not in value:
        return None
    value = dict(value)
    value.pop(ARCHIVE)
    return value


def upgrade() -> None:
    bind = op.get_bind()
    schema = settings.DB_SCHEMA
    platforms = sa.Table("platforms", sa.MetaData(), schema=schema, autoload_with=bind)
    rows = bind.execute(sa.select(platforms.c.id, platforms.c.params)).all()
    for row_id, params in rows:
        cleaned = _without_archive(params)
        if cleaned is not None:
            bind.execute(platforms.update().where(platforms.c.id == row_id).values(params=cleaned))


def downgrade() -> None:
    raise ValueError(
        "platform_rollback_snapshot_removed: 0129 deleted the 0120 archive; "
        "restore a reviewed backup instead of downgrading"
    )
