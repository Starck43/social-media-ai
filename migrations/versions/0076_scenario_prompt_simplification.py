"""replace 5 scenario prompt fields with base_prompt + media_overrides + summary_prompt

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-01

A scenario's prompts collapse to three columns (docs/CHAT_BOT_SCENARIOS.md
Phase 1):

* ``base_prompt`` — the core LLM instruction, used for every media type unless
  overridden;
* ``media_overrides`` — per-media-type overrides (``{"image": "...", ...}``);
* ``summary_prompt`` — the unified-summary prompt.

The five media-specific columns are mapped onto them: ``text_prompt`` is the
base (text was the only media the old model always analysed), ``image/video/
audio`` become override keys, ``unified_summary_prompt`` becomes the summary.
"""

from typing import Sequence, Union

from alembic import op

import sqlalchemy as sa

from app.core.config import settings

revision: str = "0076"
down_revision: Union[str, None] = "0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DROPPED = [
    "text_prompt",
    "image_prompt",
    "video_prompt",
    "audio_prompt",
    "unified_summary_prompt",
]
ADDED = [
    ("base_prompt", sa.Text()),
    ("media_overrides", sa.JSON()),
    ("summary_prompt", sa.Text()),
]


def _column_exists(table: str, column: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
            ),
            {"schema": settings.DB_SCHEMA, "table": table, "column": column},
        )
        .scalar()
    )


def _add_column_if_missing(table: str, column: str, column_type) -> None:
    """Add a column unless the table already has it.

    Alembic stamps the version *after* running the statements, so a migration
    that died part-way leaves the schema ahead of the version table; the same
    restartability guard as 0073.
    """
    if not _column_exists(table, column):
        op.add_column(table, sa.Column(column, column_type, nullable=True), schema=settings.DB_SCHEMA)


def upgrade() -> None:
    schema = settings.DB_SCHEMA
    for column, column_type in ADDED:
        _add_column_if_missing("agent_scenarios", column, column_type)

    # The data migration is idempotent: every run re-maps only the rows that
    # still carry values in the old columns, and those columns are dropped below.
    op.execute(
        f"""
        UPDATE {schema}.agent_scenarios
        SET base_prompt = COALESCE(base_prompt, text_prompt),
            summary_prompt = COALESCE(summary_prompt, unified_summary_prompt),
            media_overrides = COALESCE(media_overrides, '{{}}')
        """
    )

    # Fold the old per-media prompts into the overrides: any row that had a
    # non-text override keeps it as a JSON key, the rest get an empty object.
    op.execute(
        f"""
        UPDATE {schema}.agent_scenarios
        SET media_overrides = jsonb_strip_nulls(jsonb_build_object(
            'image', image_prompt,
            'video', video_prompt,
            'audio', audio_prompt
        )::jsonb)::json
        """
    )

    for column in DROPPED:
        op.execute(f"ALTER TABLE {schema}.agent_scenarios DROP COLUMN IF EXISTS {column}")


def downgrade() -> None:
    schema = settings.DB_SCHEMA
    for column, column_type in ADDED:
        op.execute(f"ALTER TABLE {schema}.agent_scenarios DROP COLUMN IF EXISTS {column}")

    old = [
        ("text_prompt", sa.Text()),
        ("image_prompt", sa.Text()),
        ("video_prompt", sa.Text()),
        ("audio_prompt", sa.Text()),
        ("unified_summary_prompt", sa.Text()),
    ]
    for column, column_type in old:
        _add_column_if_missing("agent_scenarios", column, column_type)

    # Reverse the mapping. text is the base, unified is the summary; the per-media
    # overrides come back out of media_overrides by key.
    op.execute(
        f"""
        UPDATE {schema}.agent_scenarios
        SET text_prompt = base_prompt,
            image_prompt = media_overrides ->> 'image',
            video_prompt = media_overrides ->> 'video',
            audio_prompt = media_overrides ->> 'audio',
            unified_summary_prompt = summary_prompt
        """
    )
