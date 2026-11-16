"""Frozen initial schema baseline captured from the reviewed 0118 schema."""

import re
from pathlib import Path

from alembic import op

revision = "baseline_0118"
down_revision = None
branch_labels = None
depends_on = None

_SCHEMA_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_PATH = Path(__file__).with_suffix(".sql")


def render_baseline_sql(schema: str) -> str:
    """Render the immutable SQL snapshot for one PostgreSQL schema identifier."""
    if not _SCHEMA_IDENTIFIER.fullmatch(schema) or len(schema) > 63:
        raise ValueError("Schema must be a valid unquoted PostgreSQL identifier")
    snapshot = _SQL_PATH.read_text(encoding="utf-8")
    return snapshot.replace("{{schema}}", f'"{schema}"')


def upgrade() -> None:
    from app.core.config import settings

    op.execute(render_baseline_sql(settings.DB_SCHEMA))


def downgrade() -> None:
    raise RuntimeError("The initial schema baseline is irreversible; restore from a reviewed backup instead.")
