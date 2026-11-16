"""Trusted, bounded cleanup of expired temporary Telegram login ciphertext.

No provider call or decryption occurs. Request keys and terminal metadata are
retained only for the bounded metadata window. Minimal owner/request tombstones
preserve replay protection after metadata removal.
"""

from datetime import timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_maker
from app.core.tenant_context import is_bypass
from app.models import TelegramLoginAttempt as Attempt
from app.models import TelegramLoginRequestKey as RequestKey

BATCH_SIZE = 200
_PENDING = ("processing", "code", "password")


async def expire_attempt_batch() -> int:
    """Fence and wipe one batch; only trusted cross-workspace maintenance may call this."""
    if not is_bypass():
        raise RuntimeError("Telegram login maintenance requires trusted bypass")
    candidates = (
        select(Attempt.id)
        .where(Attempt.state.in_(_PENDING), Attempt.expires_at <= func.clock_timestamp())
        .order_by(Attempt.expires_at, Attempt.id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    statement = (
        update(Attempt)
        .where(
            Attempt.id.in_(candidates),
            Attempt.state.in_(_PENDING),
            Attempt.expires_at <= func.clock_timestamp(),
        )
        .values(state="expired", failure_code="login_expired", secret_encrypted="", revision=Attempt.revision + 1)
        .returning(Attempt.id)
    )
    async with async_session_maker() as session, session.begin():
        result = await session.execute(statement)
        return len(result.scalars().all())


# Terminal attempt metadata only, not personal authorizations or workspace grants.
# Owner-lifetime UUID tombstones cannot be removed by this retention job: doing
# so would turn a stale request into another send-code operation.
METADATA_RETENTION = timedelta(days=30)
_TERMINAL = ("completed", "failed", "expired", "cancelled")


async def purge_attempt_batch() -> int:
    """Delete bounded terminal metadata, preserving minimal replay tombstones."""
    if not is_bypass():
        raise RuntimeError("Telegram login maintenance requires trusted bypass")
    async with async_session_maker() as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '5s'"))
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        rows = (
            await session.execute(
                select(Attempt.id, Attempt.owner_user_id, Attempt.request_key)
                .where(
                    Attempt.state.in_(_TERMINAL),
                    Attempt.secret_encrypted == "",
                    Attempt.expires_at <= func.clock_timestamp(),
                    Attempt.created_at <= func.clock_timestamp() - METADATA_RETENTION,
                )
                .order_by(Attempt.created_at, Attempt.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not rows:
            return 0
        await session.execute(
            insert(RequestKey)
            .values([{"owner_user_id": row.owner_user_id, "request_key": row.request_key} for row in rows])
            .on_conflict_do_nothing()
        )
        removed = await session.execute(
            delete(Attempt).where(Attempt.id.in_([row.id for row in rows])).returning(Attempt.id)
        )
        return len(removed.scalars().all())


async def wipe_terminal_attempt_batch() -> int:
    """Repair unexpected terminal ciphertext after outages without decrypt/replay."""
    if not is_bypass():
        raise RuntimeError("Telegram login maintenance requires trusted bypass")
    candidates = (
        select(Attempt.id)
        .where(Attempt.state.in_(_TERMINAL), Attempt.secret_encrypted != "")
        .order_by(Attempt.id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    async with async_session_maker() as session, session.begin():
        result = await session.execute(
            update(Attempt)
            .where(
                Attempt.id.in_(candidates),
                Attempt.state.in_(_TERMINAL),
                Attempt.secret_encrypted != "",
            )
            .values(secret_encrypted="", revision=Attempt.revision + 1)
            .returning(Attempt.id)
        )
        return len(result.scalars().all())
