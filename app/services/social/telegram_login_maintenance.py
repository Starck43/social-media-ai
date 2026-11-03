"""Trusted, bounded cleanup of expired temporary Telegram login ciphertext.

No provider call or decryption occurs. Request keys and terminal metadata are
retained, so cleanup never makes an old start request usable again.
"""

from sqlalchemy import func, select, update

from app.core.database import async_session_maker
from app.core.tenant_context import is_bypass
from app.models import TelegramLoginAttempt as Attempt

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
