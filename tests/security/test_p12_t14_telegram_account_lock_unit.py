"""DB-exempt guard contracts; PostgreSQL behavior has separate integration tests."""

import asyncio
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.tenant_context import tenant_scope
from app.services.social import telegram_account_lock as module
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import SourceGrantSelection, SourceGrantSnapshot

PIN = SourceGrantSelection(11, 12, 13, "synthetic-target", 14, 15, 2)
SNAPSHOT = SourceGrantSnapshot(PIN, 99)


@pytest.fixture(autouse=True)
def no_real_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DB/network forbidden in guard unit tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(module, "async_session_maker", forbidden)


class Session:
    def __init__(self):
        self.transaction = object()
        self.is_active = True
        self.commands = []
        self.outcome = None
        self.lock = True

    def get_transaction(self):
        return self.transaction

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @asynccontextmanager
    async def begin(self):
        try:
            yield
        except BaseException:
            self.outcome = "rolled_back"
            raise
        else:
            self.outcome = "committed"
        finally:
            self.transaction = None

    async def execute(self, statement):
        self.commands.append(str(statement))

    async def scalar(self, statement, params):
        self.commands.append((str(statement), params))
        if isinstance(self.lock, Exception):
            raise self.lock
        return self.lock


@pytest.fixture
def fake(monkeypatch):
    session = Session()
    inspect = AsyncMock(return_value=SNAPSHOT)
    monkeypatch.setattr(module, "async_session_maker", lambda: session)
    monkeypatch.setattr(module, "_inspect_source_grant", inspect)
    return session, inspect


@pytest.mark.parametrize("tenant,bypass", [(None, False), (13, False), (12, True), (True, False)])
async def test_scope_is_rejected_before_session(tenant, bypass):
    with tenant_scope(tenant, bypass=bypass), pytest.raises(AuthorizationUnavailable):
        async with module.locked_source_grant(PIN):
            pytest.fail("must not yield")


async def test_forged_pin_is_rejected_before_session():
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        async with module.locked_source_grant({"source_id": 11}):
            pytest.fail("must not yield")


async def test_account_not_grant_key_and_two_acquisition_checks(fake):
    session, inspect = fake
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            assert repr(guard) == "<TelegramAccountGuard>"
            assert await guard.inspect() == SNAPSHOT
    assert session.outcome == "committed"
    assert inspect.await_count == 3
    assert session.commands == [
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY",
        "SET LOCAL statement_timeout = '5s'",
        ("SELECT pg_try_advisory_xact_lock(:namespace, :account)", {"namespace": 721215, "account": 99}),
    ]
    with pytest.raises(AuthorizationUnavailable):
        await guard.inspect()


@pytest.mark.parametrize("account", [None, True, False, 0, -1, "99", 2**31])
async def test_account_key_is_validated_before_lock(fake, account):
    session, inspect = fake
    inspect.return_value = SourceGrantSnapshot(PIN, account)
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        async with module.locked_source_grant(PIN):
            pytest.fail("must not yield")
    assert session.outcome == "rolled_back" and len(session.commands) == 2


@pytest.mark.parametrize("value", [False, None, 1, "true"])
async def test_only_actual_true_lock_result_is_acquired(fake, value):
    session, inspect = fake
    session.lock = value
    with tenant_scope(12), pytest.raises(module.TelegramAccountBusy, match="^telegram_account_busy$"):
        async with module.locked_source_grant(PIN):
            pytest.fail("must not yield")
    assert session.outcome == "rolled_back" and inspect.await_count == 1


async def test_post_acquisition_account_change_rolls_back(fake):
    session, inspect = fake
    inspect.side_effect = [SNAPSHOT, SourceGrantSnapshot(PIN, 100)]
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        async with module.locked_source_grant(PIN):
            pytest.fail("must not yield")
    assert session.outcome == "rolled_back"


async def test_database_error_is_opaque_and_not_replayed(fake):
    session, inspect = fake
    session.lock = SQLAlchemyError("SYNTHETIC-PRIVATE-DIAGNOSTIC")
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable, match="^connection_unavailable$"):
        async with module.locked_source_grant(PIN):
            pytest.fail("must not yield")
    assert inspect.await_count == 1 and session.outcome == "rolled_back"


async def test_child_task_cannot_use_guard_but_does_not_invalidate_owner(fake):
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            with pytest.raises(AuthorizationUnavailable):
                await asyncio.create_task(guard.inspect())
            assert await guard.inspect() == SNAPSHOT


@pytest.mark.parametrize("changed", ["closed", "replacement", "inactive"])
async def test_changed_or_closed_transaction_cannot_reacquire(fake, changed):
    session, inspect = fake
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            if changed == "inactive":
                session.is_active = False
            else:
                session.transaction = None if changed == "closed" else object()
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()
            assert inspect.await_count == 2


@pytest.mark.parametrize("failure", [AuthorizationUnavailable(), RuntimeError("SYNTHETIC"), asyncio.CancelledError()])
async def test_failed_recheck_invalidates_handle_without_retry(fake, failure):
    session, inspect = fake
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            inspect.side_effect = failure
            expected = (
                asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else AuthorizationUnavailable
            )
            with pytest.raises(expected):
                await guard.inspect()
            inspect.side_effect = None
            inspect.return_value = SNAPSHOT
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()
            assert inspect.await_count == 3


async def test_changed_pin_recheck_invalidates_handle(fake):
    session, inspect = fake
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            inspect.return_value = SourceGrantSnapshot(replace(PIN, grant_id=16), 99)
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()


@pytest.mark.parametrize("failure", [RuntimeError("SYNTHETIC"), asyncio.CancelledError()])
async def test_caller_failure_unwinds_transaction_and_invalidates_handle(fake, failure):
    session, inspect = fake
    with tenant_scope(12), pytest.raises(type(failure)):
        async with module.locked_source_grant(PIN) as guard:
            raise failure
    assert session.outcome == "rolled_back"
    with pytest.raises(AuthorizationUnavailable):
        await guard.inspect()
