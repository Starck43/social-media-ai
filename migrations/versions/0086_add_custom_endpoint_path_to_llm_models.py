"""add custom_endpoint_path to llm_models

Revision ID: 0086
Revises: 0085
Create Date: 2026-10-08 15:52:41.064347

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision: str = "0086"
down_revision: Union[str, Sequence[str], None] = "0085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

schema = settings.DB_SCHEMA or "public"


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column(
            "custom_endpoint_path",
            sa.String(length=200),
            nullable=True,
            comment="Custom endpoint path for api_format=custom, e.g. /v1/systemone",
        ),
        schema=schema,
    )


def downgrade() -> None:
    op.drop_column("llm_models", "custom_endpoint_path", schema=schema)
