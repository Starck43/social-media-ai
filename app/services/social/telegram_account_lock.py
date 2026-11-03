"""Internal shared-account exclusion foundation, NOT a collector execution lease.

A PostgreSQL transaction advisory lock is shared by all grants for the same
personal authorization. No secret is loaded or provider contacted. It does not
fence remote requests/output, authorize an initiator/payer, or implement failover.
Do not wire the collector until those remaining boundaries are implemented.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import async_session_maker
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import (
    SourceGrantSelection,
    _inspect_source_grant,
    _validate_selection_context,
)

# Separate from Telegram login's per-actor 721214 namespace. Authorization.id is
# a positive PostgreSQL INTEGER; key by account, never tenant/source/grant.
_ACCOUNT_LOCK_NAMESPACE = 721215
_MAX_ACCOUNT_ID = 2**31 - 1


class TelegramAccountBusy(RuntimeError):
    """Another cooperating worker owns this account; no provider operation began."""

    def __init__(self):
        super().__init__("telegram_account_busy")


class TelegramAccountGuard:
    """Task/lifetime-bound lock handle. inspect() returns metadata, not I/O rights."""

    def __init__(self, session, snapshot):
        self._session = session
        self._transaction = session.get_transaction()
        self._snapshot = snapshot
        self._owner = asyncio.current_task()
        self._active = True

    def __repr__(self):
        return "<TelegramAccountGuard>"

    async def inspect(self):
        """Recheck the complete pin on the still-owned READ COMMITTED connection.

        Cross-task, post-exit, closed/changed transactions and lost connections
        cannot silently start another transaction or reacquire a lock. A failed
        recheck permanently invalidates this handle, even if the caller catches it.
        A snapshot can still become stale immediately after returning.
        """
        if (
            not self._active
            or asyncio.current_task() is not self._owner
            or self._session.get_transaction() is not self._transaction
            or not self._session.is_active
        ):
            raise AuthorizationUnavailable()
        try:
            snapshot = await _inspect_source_grant(self._session, self._snapshot.selection)
            if snapshot != self._snapshot:
                raise AuthorizationUnavailable()
            return snapshot
        except asyncio.CancelledError:
            self._active = False
            raise
        except Exception:
            self._active = False
            raise AuthorizationUnavailable() from None


async def _acquire(session, selection):
    try:
        # READ COMMITTED is essential for subsequent fresh checks in this same
        # transaction. READ ONLY prevents using this connection to settle output.
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout = '5s'"))
        snapshot = await _inspect_source_grant(session, selection)
        account_id = snapshot.authorization_id
        if type(account_id) is not int or not 0 < account_id <= _MAX_ACCOUNT_ID:
            raise AuthorizationUnavailable()
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:namespace, :account)"),
            {"namespace": _ACCOUNT_LOCK_NAMESPACE, "account": account_id},
        )
        if acquired is not True:
            raise TelegramAccountBusy()
        # The grant/source may have changed between discovery and the lock.
        if await _inspect_source_grant(session, selection) != snapshot:
            raise AuthorizationUnavailable()
        return TelegramAccountGuard(session, snapshot)
    except SQLAlchemyError:
        raise AuthorizationUnavailable() from None


@asynccontextmanager
async def locked_source_grant(selection: SourceGrantSelection):
    """Fail-fast account lock with fresh pre/post-acquisition metadata checks.

    Only cooperating users of this API serialize. Revoke/detach do NOT wait for
    this advisory lock: the holder must notice them on its next inspect(). No
    TTL, renewal or automatic reclaim is invented; PostgreSQL owns release at
    transaction/connection end. Caller cancellation/error unwinds the transaction.
    Keep lifetimes bounded at the future execution adapter. DB loss is NOT proof
    that a previously sent provider operation did not happen; never auto-replay.
    """
    _validate_selection_context(selection)
    async with async_session_maker() as session, session.begin():
        guard = await _acquire(session, selection)
        try:
            yield guard
        finally:
            guard._active = False
