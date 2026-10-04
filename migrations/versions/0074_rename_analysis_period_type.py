"""rename analysis_period_type values to match the PeriodType members

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-04

The PostgreSQL enum still carried the pre-stage-1 names (`DAILY`, `WEEKLY`,
`MONTHLY`) while `PeriodType` had been renamed to `DAY`/`WEEK`/`MONTH`. The
column stores by name (`store_as_name=True`), so every write of an analysis row
failed: the model sent `DAY`, the database only accepted `DAILY`.

Renaming the type in the database rather than the enum back, because the short
names are the ones the rest of the code, the digest periods and the admin UI
already use. PostgreSQL cannot rename an enum value in place, so the type is
rebuilt: a new one is created, existing values are cast into it, and the old
type is dropped. The column type is restored in the same transaction, which
rewrites the table.
"""

from typing import Sequence, Union

from alembic import op

import sqlalchemy as sa

from app.core.config import settings

revision: str = "0074"
down_revision: Union[str, None] = "0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENUM_NAME = "analysis_period_type"
RENAME = {"DAILY": "DAY", "WEEKLY": "WEEK", "MONTHLY": "MONTH"}


# The labels this migration owns, in a fixed order, so the rebuild does not
# depend on reading whatever the type happens to hold right now. Deriving the
# new type from the live labels instead would silently keep whatever a previous,
# half-applied run left behind — the cast would then find its own input already
# renamed and fail with "invalid input value ... DAILY", which is exactly how the
# first attempt died.
_SOURCE_ORDER = ("DAILY", "WEEKLY", "MONTHLY", "CUSTOM")


def _current_values() -> list[str]:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = :name AND n.nspname = :schema ORDER BY e.enumsortorder"
        ),
        {"name": ENUM_NAME, "schema": settings.DB_SCHEMA},
    )
    return [r[0] for r in rows]


def _rebuild(target: dict[str, str]) -> None:
    """Rebuild the enum, mapping the source labels onto their target labels.

    Restartable: a run that died part-way leaves either the old type or the new
    one, and both are handled — the labels are matched against the known source
    set rather than trusted from the database, so a repeat produces the same
    result instead of failing on its own output.
    """
    schema = settings.DB_SCHEMA
    existing = _current_values()
    if not existing:
        return
    if all(v in target for v in existing):
        return  # already renamed; nothing left to do

    labels = [v for v in _SOURCE_ORDER]
    for extra in existing:
        if extra not in labels:
            labels.append(extra)

    temp = f"{ENUM_NAME}_tmp"
    op.execute(f"DROP TYPE IF EXISTS {schema}.{temp}")
    values = ", ".join(f"'{target.get(v, v)}'" for v in labels)
    op.execute(f"CREATE TYPE {schema}.{temp} AS ENUM ({values})")

    # Every label is mapped explicitly. The obvious-looking
    # `USING period_type::text::temp` does NOT rename: the text is fed to the new
    # type verbatim, so the stored "DAILY" simply is not among its labels and the
    # whole ALTER fails. Only a CASE re-labels the values.
    cases = " ".join(f"WHEN '{src_label}' THEN '{dst}'" for src_label, dst in target.items())
    op.execute(
        f"ALTER TABLE {schema}.ai_analytics ALTER COLUMN period_type TYPE {schema}.{temp} "
        f"USING (CASE period_type::text {cases} ELSE period_type::text END)::{schema}.{temp}"
    )
    op.execute(f"DROP TYPE {schema}.{ENUM_NAME}")
    op.execute(f"ALTER TYPE {schema}.{temp} RENAME TO {ENUM_NAME}")


def upgrade() -> None:
    _rebuild(RENAME)


def downgrade() -> None:
    _rebuild({v: k for k, v in RENAME.items()})
