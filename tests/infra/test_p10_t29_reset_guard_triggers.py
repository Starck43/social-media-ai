"""P10-T29: the destructive reset must survive migration guard triggers.

Migrations 0093–0100 install ``BEFORE TRUNCATE`` statement triggers that
``RAISE`` on the immutable billing-evidence tables. A test schema aligned
through those migrations used to make ``setup_test_db --reset`` abort with
"Budget reservation is immutable" mid-``TRUNCATE``. The reset now disables
exactly those triggers around the ``TRUNCATE`` and re-enables them before the
transaction commits, so the installed guards are unchanged for readers such as
``LLMBudgetManager.reserve_receipt``.
"""

from __future__ import annotations

import secrets

from sqlalchemy import text

from app.core.database import async_engine
from scripts.setup_test_db import _truncate_all


def _schema_name() -> str:
    return f"reset_guard_{secrets.token_hex(3)}"


async def test_truncate_all_survives_no_truncate_guard_triggers():
    schema = _schema_name()
    # Committed scratch schema in the shape a migration-aligned test schema
    # has: a table carrying a BEFORE TRUNCATE guard that refuses truncation.
    async with async_engine.begin() as conn:
        await conn.execute(text(f'create schema "{schema}"'))
        await conn.execute(text(f'create table "{schema}".items (id serial primary key, note text)'))
        await conn.execute(
            text(
                f'create function "{schema}".reject_truncate() returns trigger language plpgsql as $$ '
                "begin raise exception 'evidence rows are immutable' using errcode = '55000'; end; $$"
            )
        )
        await conn.execute(
            text(
                f'create trigger items_no_truncate before truncate on "{schema}".items '
                f'for each statement execute function "{schema}".reject_truncate()'
            )
        )
        await conn.execute(text(f"insert into \"{schema}\".items (note) values ('synthetic')"))

    try:
        await _truncate_all(schema)

        # The row is gone and the guard is re-enabled before the reset commits.
        async with async_engine.begin() as conn:
            remaining = (await conn.execute(text(f'select count(*) from "{schema}".items'))).scalar_one()
            assert remaining == 0
            state = (
                await conn.execute(
                    text(
                        "select t.tgenabled::text from pg_catalog.pg_trigger t "
                        "join pg_catalog.pg_class c on c.oid = t.tgrelid "
                        "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                        "where n.nspname = :schema and c.relname = 'items' "
                        "and t.tgname = 'items_no_truncate'"
                    ),
                    {"schema": schema},
                )
            ).scalar_one()
            assert state == "O"

            # The guard still refuses a plain truncate right after the reset.
            try:
                await conn.execute(text(f'truncate table "{schema}".items'))
            except Exception as exc:
                assert "evidence rows are immutable" in str(exc)
            else:
                raise AssertionError("the guard trigger failed to refuse the truncate")
    finally:
        # Drop and verify the scratch objects are gone.
        async with async_engine.begin() as conn:
            await conn.execute(text(f'drop schema "{schema}" cascade'))
    async with async_engine.connect() as conn:
        exists = (
            await conn.execute(text("select 1 from pg_namespace where nspname = :schema"), {"schema": schema})
        ).scalar_one_or_none()
    assert exists is None
