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


@pytest.fixture
def material_resolver(fake, monkeypatch):
    values = {"api_id": 123, "api_hash": "SYNTHETIC-HASH", "session": "SYNTHETIC-NOT-A-SESSION"}
    resolver = AsyncMock(return_value=(SNAPSHOT, values))
    monkeypatch.setattr(module, "_resolve_source_grant_material", resolver)
    return *fake, resolver, values


async def test_setup_material_rechecks_after_resolution_and_clears_on_scope_exit(material_resolver, caplog):
    session, inspect, resolver, values = material_resolver
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            async with guard._setup_material() as material:
                resolver.assert_awaited_once_with(session, SNAPSHOT)
                assert inspect.await_count == 3
                assert material.api_id == 123
                assert material.api_hash == "SYNTHETIC-HASH"
                assert material.session == "SYNTHETIC-NOT-A-SESSION"
                assert repr(material) == "<TelegramSetupMaterial>"
                assert "SYNTHETIC" not in repr(guard) + repr(material) + caplog.text
            assert values == {}
            with pytest.raises(AuthorizationUnavailable):
                _ = material.session
            assert await guard.inspect() == SNAPSHOT
    assert session.outcome == "committed"


async def test_material_cannot_be_copied_pickled_or_dataclass_serialized(material_resolver):
    import copy
    import pickle
    from dataclasses import asdict

    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard, guard._setup_material() as material:
            for operation in (copy.copy, copy.deepcopy, pickle.dumps, asdict, vars):
                with pytest.raises(TypeError):
                    operation(material)


async def test_nested_material_scope_is_rejected_without_closing_owner_scope(material_resolver):
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard, guard._setup_material() as material:
            with pytest.raises(AuthorizationUnavailable):
                async with guard._setup_material():
                    pytest.fail("no nested material scope")
            assert material.api_id == 123


async def test_child_task_cannot_read_material_or_resolve_another_scope(material_resolver):
    async def child(guard, material):
        with pytest.raises(AuthorizationUnavailable):
            _ = material.session
        with pytest.raises(AuthorizationUnavailable):
            async with guard._setup_material():
                pytest.fail("child cannot resolve")

    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard, guard._setup_material() as material:
            await asyncio.create_task(child(guard, material))
            assert material.api_id == 123
    assert material_resolver[2].await_count == 1


@pytest.mark.parametrize("changed", ["closed", "replacement", "inactive", "tenant", "bypass"])
async def test_local_lifetime_or_context_loss_clears_material_permanently(material_resolver, changed):
    session, inspect, resolver, values = material_resolver
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard, guard._setup_material() as material:
            original = session.transaction
            if changed == "inactive":
                session.is_active = False
            elif changed in ("closed", "replacement"):
                session.transaction = None if changed == "closed" else object()
            with tenant_scope(13 if changed == "tenant" else 12, bypass=changed == "bypass"):
                with pytest.raises(AuthorizationUnavailable):
                    _ = material.session
            session.transaction, session.is_active = original, True
            assert values == {}
            with pytest.raises(AuthorizationUnavailable):
                _ = material.api_id
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()


@pytest.mark.parametrize("stage", ["resolve", "postcheck"])
@pytest.mark.parametrize(
    "failure", [AuthorizationUnavailable(), RuntimeError("SYNTHETIC-SECRET"), asyncio.CancelledError()]
)
async def test_setup_failures_are_opaque_cancel_safe_and_never_replayed(material_resolver, stage, failure, caplog):
    session, inspect, resolver, values = material_resolver
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            (resolver if stage == "resolve" else inspect).side_effect = failure
            expected = (
                asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else AuthorizationUnavailable
            )
            with pytest.raises(expected) as error:
                async with guard._setup_material():
                    pytest.fail("failed setup must not yield")
            assert "SYNTHETIC" not in str(error.value) + repr(error.value) + caplog.text
            resolver.side_effect = inspect.side_effect = None
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()
            with pytest.raises(AuthorizationUnavailable):
                async with guard._setup_material():
                    pytest.fail("failed handle must not restart")
            assert resolver.await_count == 1
            if stage == "postcheck":
                assert values == {}


async def test_resolved_different_account_clears_material_and_invalidates_guard(material_resolver):
    session, inspect, resolver, values = material_resolver
    resolver.return_value = SourceGrantSnapshot(PIN, 100), values
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard:
            with pytest.raises(AuthorizationUnavailable):
                async with guard._setup_material():
                    pytest.fail("account changed")
            assert values == {} and inspect.await_count == 2
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()


@pytest.mark.parametrize("failure", [RuntimeError("SYNTHETIC-CALLER"), asyncio.CancelledError()])
async def test_material_body_failure_preserves_error_and_clears_scope(material_resolver, failure):
    session, inspect, resolver, values = material_resolver
    with tenant_scope(12), pytest.raises(type(failure)) as error:
        async with module.locked_source_grant(PIN) as guard, guard._setup_material() as material:
            raise failure
    assert error.value is failure and values == {} and session.outcome == "rolled_back"
    with pytest.raises(AuthorizationUnavailable):
        _ = material.session


async def test_failed_later_inspect_clears_already_resolved_material(material_resolver):
    session, inspect, resolver, values = material_resolver
    with tenant_scope(12):
        async with module.locked_source_grant(PIN) as guard, guard._setup_material() as material:
            inspect.side_effect = AuthorizationUnavailable()
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()
            assert values == {}
            with pytest.raises(AuthorizationUnavailable):
                _ = material.session


async def test_no_material_resolution_before_lock_or_after_exit(material_resolver):
    session, inspect, resolver, values = material_resolver
    with tenant_scope(12):
        session.lock = False
        with pytest.raises(module.TelegramAccountBusy):
            async with module.locked_source_grant(PIN):
                pytest.fail("busy")
        resolver.assert_not_awaited()
        session.lock = True
        session.transaction = object()
        async with module.locked_source_grant(PIN) as guard:
            pass
        with pytest.raises(AuthorizationUnavailable):
            async with guard._setup_material():
                pytest.fail("exited guard")
        resolver.assert_not_awaited()
