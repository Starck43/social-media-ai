"""DB-exempt maintenance contract tests; SQL is compiled but never executed."""

import asyncio
import socket
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.tenant_context import is_bypass, tenant_scope
from app.jobs import dispatcher
from app.services.social import telegram_login_maintenance as maintenance


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("network forbidden in source-only tests")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


class FakeSession:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.statement = rows, error, None
        self.transaction = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @asynccontextmanager
    async def begin(self):
        try:
            yield
        except BaseException:
            self.transaction = "rolled_back"
            raise
        else:
            self.transaction = "committed"

    async def execute(self, statement):
        self.statement = statement
        if self.error:
            raise self.error
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows


@pytest.mark.asyncio
async def test_requires_explicit_trusted_bypass_before_opening_session(monkeypatch):
    factory = AsyncMock(side_effect=AssertionError("must not open"))
    monkeypatch.setattr(maintenance, "async_session_maker", factory)
    with tenant_scope(7), pytest.raises(RuntimeError, match="trusted bypass"):
        await maintenance.expire_attempt_batch()
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_batch_is_locked_bounded_rechecked_and_does_not_return_ciphertext(monkeypatch):
    session = FakeSession(["fake-id-1", "fake-id-2"])
    monkeypatch.setattr(maintenance, "async_session_maker", lambda: session)
    with tenant_scope(bypass=True):
        assert await maintenance.expire_attempt_batch() == 2
    assert session.transaction == "committed"
    compiled = session.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True})
    sql = str(compiled)
    assert "UPDATE test_schema.telegram_login_attempts SET" in sql
    assert sql.count("clock_timestamp()") == 2
    assert "FOR UPDATE SKIP LOCKED" in sql and "LIMIT" in sql
    assert "revision=(test_schema.telegram_login_attempts.revision +" in sql
    assert "RETURNING test_schema.telegram_login_attempts.id" in sql
    assert "secret_encrypted" not in sql.split("RETURNING", 1)[1]
    assert set(compiled.params.values()) >= {"processing", "code", "password", "expired", "login_expired", ""}
    assert maintenance.BATCH_SIZE in compiled.params.values()


@pytest.mark.asyncio
async def test_transaction_error_rolls_back_and_propagates(monkeypatch):
    session = FakeSession(error=RuntimeError("synthetic"))
    monkeypatch.setattr(maintenance, "async_session_maker", lambda: session)
    with tenant_scope(bypass=True), pytest.raises(RuntimeError, match="synthetic"):
        await maintenance.expire_attempt_batch()
    assert session.transaction == "rolled_back"


@pytest.mark.asyncio
async def test_background_loop_retries_drains_full_batch_and_redacts_errors(monkeypatch, caplog):
    bypasses, sleeps = [], []
    steps = iter([RuntimeError("FAKE-PRIVATE-VALUE"), maintenance.BATCH_SIZE, 0])

    async def fake_expire():
        bypasses.append(is_bypass())
        value = next(steps)
        if isinstance(value, Exception):
            raise value
        return value

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(maintenance, "expire_attempt_batch", fake_expire)
    monkeypatch.setattr(dispatcher.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await dispatcher._telegram_login_maintenance_loop()
    assert bypasses == [True] * 3 and sleeps == [60, 1, 60]
    assert not is_bypass()
    assert "telegram_login_maintenance_failed" in caplog.text
    assert "FAKE-PRIVATE-VALUE" not in caplog.text


@pytest.mark.asyncio
async def test_worker_shutdown_cancels_and_awaits_background_cleanup(monkeypatch):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def background():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def worker(_):
        await started.wait()
        raise asyncio.CancelledError()

    monkeypatch.setattr(dispatcher, "_telegram_login_maintenance_loop", background)
    monkeypatch.setattr(dispatcher, "_worker_loop", worker)
    with pytest.raises(asyncio.CancelledError):
        await dispatcher.worker_forever()
    assert stopped.is_set()
