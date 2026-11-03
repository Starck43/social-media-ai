"""Private bounded runtime epoch and same-transaction output gate.

An epoch is not a reusable permit. Before/after every application RPC, recheck
the original lock connection, consent, epoch, job generation and execution actor.
A request already sent cannot be recalled; withdrawn/late responses are dropped.
Used only by the default-OFF collector path with same-transaction staging.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import select, update

from app.models import PersonalConnectorAuthorization as Authorization
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_account_lock import locked_source_grant
from app.services.social.telegram_runtime_access import _capture_execution, _check_execution

_MAX_GENERATION = 2**63 - 1
_MAX_RUNTIME_SECONDS = 120
_MAX_REQUEST_SECONDS = 10
_MAX_REQUESTS = 100


class _RuntimeFence:
    __slots__ = ("_guard", "_pin", "_generation", "_subject_key", "_deadline", "_requests", "_closed", "_admitting")

    def __init__(self, guard, pin, generation, subject_key, deadline):
        self._guard = guard
        self._pin = pin
        self._generation = generation
        self._subject_key = subject_key
        self._deadline = deadline
        self._requests = 0
        self._closed = False
        self._admitting = False

    def __repr__(self):
        return "<TelegramRuntimeFence>"

    def __reduce_ex__(self, protocol):
        raise TypeError("telegram_runtime_not_serializable")

    def _close(self):
        self._closed = True
        self._guard._invalidate()

    def _require_owner(self):
        self._guard._require_owner()
        if self._closed or asyncio.get_running_loop().time() >= self._deadline:
            self._close()
            raise AuthorizationUnavailable()

    async def _check_generation(self, session):
        snapshot = self._guard._snapshot
        generation = await session.scalar(
            select(Authorization.execution_generation).where(
                Authorization.id == snapshot.authorization_id,
                Authorization.owner_user_id == snapshot.selection.owner_user_id,
                Authorization.execution_generation == self._generation,
                Authorization.subject_key == self._subject_key,
            )
        )
        if generation != self._generation:
            raise AuthorizationUnavailable()

    async def _inspect(self):
        self._require_owner()
        try:
            await self._guard.inspect()
            await self._check_generation(self._guard._session)
            await _check_execution(self._guard._session, self._pin)
            await self._guard.inspect()
            self._require_owner()
        except asyncio.CancelledError:
            self._close()
            raise
        except Exception:
            self._close()
            raise AuthorizationUnavailable() from None

    @asynccontextmanager
    async def _request(self):
        """Await in the OWNER task; wait_for/create_task would lose ownership."""
        self._require_owner()
        if self._admitting or self._requests >= _MAX_REQUESTS:
            self._close()
            raise AuthorizationUnavailable()
        self._requests += 1
        try:
            async with asyncio.timeout(_MAX_REQUEST_SECONDS):
                await self._inspect()
                yield
                await self._inspect()
        except asyncio.CancelledError:
            self._close()
            raise
        except Exception:
            self._close()
            raise AuthorizationUnavailable() from None

    @asynccontextmanager
    async def _output_transaction(self):
        """Only DB writes in THIS transaction are fenced; no external I/O.

        The consent gate holds the account FOR SHARE until commit. Advancing an
        epoch needs UPDATE on that row, so a successor cannot invalidate this
        epoch midway through a successful admission. Job/actor/payer rows stay
        locked, too. Check again after callback writes, not only before them.
        Unknown commits are opaque and MUST NOT trigger automatic replay.
        """
        await self._inspect()
        if self._admitting:
            raise AuthorizationUnavailable()
        self._admitting = True
        try:
            async with self._guard._admission_transaction() as session:
                await self._check_generation(session)
                await _check_execution(session, self._pin, lock=True)
                self._require_owner()
                yield session
                await self._check_generation(session)
                await _check_execution(session, self._pin, lock=True)
                await self._inspect()
        except asyncio.CancelledError:
            self._close()
            raise
        except Exception:
            self._close()
            raise AuthorizationUnavailable() from None
        finally:
            self._admitting = False


@asynccontextmanager
async def _locked_runtime(selection, claim):
    """Private code-only foundation; neither HTTP nor raw-client execution API."""
    fence = None
    try:
        async with asyncio.timeout(_MAX_RUNTIME_SECONDS):
            deadline = asyncio.get_running_loop().time() + _MAX_RUNTIME_SECONDS
            async with locked_source_grant(selection) as guard:
                pin = await _capture_execution(guard._session, selection, claim)
                if pin.authorization_id != guard._snapshot.authorization_id:
                    raise AuthorizationUnavailable()
                # Allocate on a separate WRITE connection while the account
                # advisory transaction is owned. Do not decrypt before commit.
                async with guard._admission_transaction() as session:
                    await _check_execution(session, pin, lock=True)
                    row = (
                        await session.execute(
                            update(Authorization)
                            .where(
                                Authorization.id == guard._snapshot.authorization_id,
                                Authorization.owner_user_id == selection.owner_user_id,
                                Authorization.execution_generation >= 0,
                                Authorization.execution_generation < _MAX_GENERATION,
                            )
                            .values(execution_generation=Authorization.execution_generation + 1)
                            .returning(Authorization.execution_generation, Authorization.subject_key)
                        )
                    ).one_or_none()
                    if row is None:
                        raise AuthorizationUnavailable()
                    generation, subject_key = row
                    if type(generation) is not int or generation <= 0 or not subject_key:
                        raise AuthorizationUnavailable()
                    await _check_execution(session, pin, lock=True)
                fence = _RuntimeFence(guard, pin, generation, subject_key, deadline)
                await fence._inspect()
                yield fence
    except asyncio.CancelledError:
        raise
    except Exception:
        raise AuthorizationUnavailable() from None
    finally:
        if fence is not None:
            fence._close()
