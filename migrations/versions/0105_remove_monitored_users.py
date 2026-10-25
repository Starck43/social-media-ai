"""Remove the retired per-source / per-task author list.

`monitored_users` was never a selection: on a task run it replaced a source's
wall with the personal walls of the listed people, and a listed user only
worked when a separate ``USER`` source already existed. The replacement is a
task ``tracked_users`` filter over the task's own sources
(docs/design/task-selection.md).

The owner decided pre-production to drop the stored values outright rather than
convert them: nobody has re-entered a selection yet, and an automatic
conversion would guess which ``USER`` source belongs to which person.

Only two JSON columns are touched. No table, column or constraint changes, and
the downgrade deliberately does nothing: the removed configuration is not
reconstructable and restoring the retired key would recreate a setting no code
reads.

Revision ID: 0105
Revises: 0104
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from app.core.config import settings

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA

# Both columns are JSON objects: strip the key with the JSON operators so a
# malformed or non-object value is left exactly as it is.
def _strip(column: str, key: str) -> str:
    return f"({column})::jsonb - '{key}'"


def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(SCHEMA)
    for table, column in (("agent_tasks", "payload"), ("sources", "params")):
        bind.execute(
            text(
                f"UPDATE {schema}.{table} "  # noqa: S608 — identifiers are code constants
                f"SET {column} = {_strip(column, 'monitored_users')} "
                f"WHERE jsonb_typeof(({column})::jsonb) = 'object' "
                f"AND ({column})::jsonb ? 'monitored_users'"
            )
        )


def downgrade() -> None:
    # Intentionally empty: the removed key is not reconstructable and no code
    # reads it. See the module docstring.
    pass
