"""P8-T51: fit the long default job types in both scheduling columns.

`DEFAULT_TASKS` contains `comms.outbox.dispatch`, `documents.approvals.expire`
and `planner.reminders.dispatch`, which are longer than varchar(20). Widening
both scheduling columns is metadata-only in PostgreSQL: no stored value changes.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p8t51_job_type_width"
down_revision = "p12t15_policy_audit"
branch_labels = None
depends_on = None

_TABLES = ("agent_tasks", "jobs")
_OLD_WIDTH = 20
_NEW_WIDTH = 64


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "job_type",
            existing_type=sa.String(_OLD_WIDTH),
            type_=sa.String(_NEW_WIDTH),
            existing_nullable=False,
            schema=settings.DB_SCHEMA,
        )


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "job_type",
            existing_type=sa.String(_NEW_WIDTH),
            type_=sa.String(_OLD_WIDTH),
            existing_nullable=False,
            schema=settings.DB_SCHEMA,
        )
