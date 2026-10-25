"""Shared, cookie-independent login throttling (P10-T04).

The budget is counted per normalized account identifier and per client address,
and it lives in PostgreSQL, so the console, the web pages and the API all spend
from the same counter no matter which process handles the request. A cookie is
never part of the key: clearing cookies does not reset progress.

Callers treat every refusal the same way: answer the generic credentials error.
`LoginLocked` is not a signal to tell the visitor that the account exists.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.hashing import get_password_hash, verify_password
from app.models.identity.login_attempt import ACCOUNT_SCOPE, ADDRESS_SCOPE, LoginAttempt

logger = logging.getLogger(__name__)

#: A real hash verified for unknown accounts, so the failure path costs the same
#: as a wrong password for an existing one. Built on first use, once per process.
_DUMMY_HASH: str | None = None


class LoginLocked(Exception):
    """Either budget is exhausted; the caller must answer the generic error."""


def normalize_identifier(username_or_email: str | None) -> str:
    """Casefold and bound the key so `User.Name` and `user.name ` share a budget."""

    return (username_or_email or "").strip().casefold()[:255]


def client_address(request) -> str | None:
    """The address the shared address budget is keyed on, if the client sent one."""

    client = getattr(request, "client", None)
    return getattr(client, "host", None) or None


def dummy_hash() -> str:
    """The process-wide placeholder hash; a real bcrypt value, never a literal."""

    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = get_password_hash(secrets.token_urlsafe(24))
    return _DUMMY_HASH


async def verify_dummy_password(password: str) -> None:
    """Spend the same work as a real verification without touching an account."""

    verify_password(password, dummy_hash())


def _new_count(window_start: datetime):
    """Restart the count when the previous attempt fell out of the window."""

    return case((LoginAttempt.last_attempt_at < window_start, 1), else_=LoginAttempt.failed_count + 1)


async def _record(session, scope: str, identifier: str, limit: int, now: datetime) -> None:
    """Atomic upsert of one key: count the failure and lock at the threshold."""

    window_start = now - timedelta(minutes=settings.LOGIN_TIMEOUT_MINUTES)
    locked_until = now + timedelta(minutes=settings.LOGIN_TIMEOUT_MINUTES)
    count = _new_count(window_start)
    statement = insert(LoginAttempt).values(
        scope=scope,
        identifier=identifier,
        failed_count=1,
        last_attempt_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[LoginAttempt.scope, LoginAttempt.identifier],
        set_={
            "failed_count": count,
            "last_attempt_at": now,
            "locked_until": case((count >= limit, locked_until), else_=LoginAttempt.locked_until),
        },
    )
    await session.execute(statement)


async def guard_login(username_or_email: str, address: str | None = None) -> None:
    """Raise `LoginLocked` when the account or the address is out of budget."""

    keys = [(ACCOUNT_SCOPE, normalize_identifier(username_or_email))]
    if address:
        keys.append((ADDRESS_SCOPE, address))
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        rows = await session.execute(
            select(LoginAttempt.scope, LoginAttempt.identifier, LoginAttempt.locked_until).where(
                or_(
                    *[
                        (LoginAttempt.scope == scope) & (LoginAttempt.identifier == identifier)
                        for scope, identifier in keys
                    ]
                )
            )
        )
        locked = [row.scope for row in rows if row.locked_until is not None and row.locked_until > now]
    if locked:
        logger.warning("login refused by shared throttle scope=%s", ",".join(sorted(locked)))
        raise LoginLocked()


async def record_login_failure(username_or_email: str, address: str | None = None) -> None:
    """Count one refused attempt for the account and, when known, the address."""

    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        await _record(session, ACCOUNT_SCOPE, normalize_identifier(username_or_email), settings.MAX_LOGIN_ATTEMPTS, now)
        if address:
            await _record(session, ADDRESS_SCOPE, address, settings.LOGIN_IP_MAX_ATTEMPTS, now)
        await session.commit()


async def clear_login_failures(username_or_email: str) -> None:
    """A proven password clears the account budget; the address budget decays."""

    async with async_session_maker() as session:
        await session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.scope == ACCOUNT_SCOPE,
                LoginAttempt.identifier == normalize_identifier(username_or_email),
            )
        )
        await session.commit()
