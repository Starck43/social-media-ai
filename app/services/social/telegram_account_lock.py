"""Internal shared-account exclusion foundation, NOT a collector execution lease.

A PostgreSQL transaction advisory lock is shared by all grants for the same
personal authorization. Its private setup-material scope validates the envelope;
no provider is contacted. It does not fence remote requests/output, authorize an
initiator/payer, or implement failover.
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
    _resolve_source_grant_material,
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


class _TelegramSetupMaterial:
    """Non-serializable, task/scope-bound setup values, NOT execution permission.

    Access checks local ownership/lifetime only, not fresh database state. Python
    strings already read by a trusted adapter cannot be recalled or zeroized.
    Never expose this object or its values in an API, logging or persisted job.
    """

    __slots__ = ("_guard", "_values")

    def __init__(self, guard, values):
        self._guard = guard
        self._values = values

    def __repr__(self):
        return "<TelegramSetupMaterial>"

    def __reduce_ex__(self, protocol):
        raise TypeError("telegram_setup_material_not_serializable")

    def _close(self):
        self._values.clear()

    def _read(self, key):
        self._guard._require_owner()
        try:
            _validate_selection_context(self._guard._snapshot.selection)
        except AuthorizationUnavailable:
            self._guard._invalidate()
            raise
        if not self._values:
            raise AuthorizationUnavailable()
        return self._values[key]

    api_id = property(lambda self: self._read("api_id"))
    api_hash = property(lambda self: self._read("api_hash"))
    session = property(lambda self: self._read("session"))


class TelegramAccountGuard:
    """Task/lifetime-bound lock handle. inspect() returns metadata, not I/O rights."""

    def __init__(self, session, snapshot):
        self._session = session
        self._transaction = session.get_transaction()
        self._snapshot = snapshot
        self._owner = asyncio.current_task()
        self._active = True
        self._material = None

    def __repr__(self):
        return "<TelegramAccountGuard>"

    def _invalidate(self):
        self._active = False
        if self._material is not None:
            self._material._close()
            self._material = None

    def _require_owner(self):
        if asyncio.current_task() is not self._owner:
            raise AuthorizationUnavailable()
        if not self._active or self._session.get_transaction() is not self._transaction or not self._session.is_active:
            self._invalidate()
            raise AuthorizationUnavailable()

    async def inspect(self):
        """Recheck the pin on the still-owned READ COMMITTED transaction.

        Cross-task, post-exit, closed/changed transactions and lost connections
        cannot silently start another transaction or reacquire a lock. A failed
        recheck invalidates the handle. A snapshot may become stale on return.
        """
        self._require_owner()
        try:
            snapshot = await _inspect_source_grant(self._session, self._snapshot.selection)
            if snapshot != self._snapshot:
                raise AuthorizationUnavailable()
            return snapshot
        except asyncio.CancelledError:
            self._invalidate()
            raise
        except Exception:
            self._invalidate()
            raise AuthorizationUnavailable() from None

    @asynccontextmanager
    async def _setup_material(self):
        """Internal, bounded-by-caller setup scope; never a collector I/O lease.

        Resolve only while this task owns the lock, atomically with the complete
        source/grant pin, then recheck after decryption. No account discovery or
        legacy fallback. A later request must have its own fresh boundary check.
        """
        self._require_owner()
        if self._material is not None:
            raise AuthorizationUnavailable()
        material = None
        try:
            _validate_selection_context(self._snapshot.selection)
            snapshot, values = await _resolve_source_grant_material(self._session, self._snapshot)
            # Register before any subsequent await so failures/cancellation clear
            # our retained plaintext even if the post-decryption check fails.
            material = _TelegramSetupMaterial(self, values)
            self._material = material
            if snapshot != self._snapshot:
                raise AuthorizationUnavailable()
            await self.inspect()
        except asyncio.CancelledError:
            self._invalidate()
            raise
        except Exception:
            self._invalidate()
            raise AuthorizationUnavailable() from None
        try:
            yield material
        except BaseException:
            self._invalidate()
            raise
        finally:
            material._close()
            if self._material is material:
                self._material = None


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
            guard._invalidate()
