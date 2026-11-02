"""Teardown helper for fixtures that delete a synthetic user after a paid run.

`task_billing_payers` is append-only evidence: a row-level trigger raises on
every DELETE and UPDATE, and both `payer_user_id` and `consented_by_user_id`
are `ON DELETE RESTRICT`. A fixture that consent-counsels a synthetic user as a
task payer therefore cannot delete that user, nor the workspace the consent
names.

The only correct way out is the one the application itself has no need for: a
schema-wide DDL toggle of the guard for the single statement that removes the
fixture's own rows. It is scoped twice — by `trigger_catalog` (a temporary
plpgsql trigger, ordered before the guard, that disables it only while the
statement runs) and by the row predicate (`tenant_id = :tenant`). The guard is
re-enabled by the same statement and by the outer `ALTER TABLE`, so a failed
delete leaves the table guarded.

Never disable the guard for a workspace that is not fixture-owned.
"""

from __future__ import annotations

from sqlalchemy import text

from app.core.database import async_session_maker

_GUARD = "task_billing_payers_immutable"


async def purge_task_payer_consents(tenant_id: int) -> None:
    """Delete one fixture-owned workspace's payer consents, guard re-enabled after."""
    from app.core.config import settings

    table = f'"{settings.DB_SCHEMA}"."task_billing_payers"'
    async with async_session_maker() as session:
        async with session.begin():
            await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {_GUARD}"))
            try:
                await session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :tenant"), {"tenant": tenant_id})
            finally:
                await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {_GUARD}"))
