"""drop timezone from agent_tasks

The column duplicated a value that never disagreed with `tenants.timezone`;
the workspace zone is now resolved in one place (`app/tasks/cron.py::resolve_tz`)
and `SCHEDULER_TIMEZONE` is only the fallback for a workspace without one.

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op

import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("agent_tasks", "timezone")


def downgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Europe/Moscow",
        ),
    )
