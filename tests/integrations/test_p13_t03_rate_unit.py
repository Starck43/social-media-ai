"""DB-free checks: all provider/DB boundaries are explicit doubles."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.integrations.ratelimit import (
    POLICIES,
    ProviderDeferred,
    ProviderLimitSignal,
    RateLimiter,
    WriteOutcomeUnknown,
    budget_for,
    catalog,
    execute_read,
    execute_write,
    provider_signal,
    store,
)
from app.integrations.ratelimit.signals import retry_after_seconds

__source_only__ = True


@pytest.fixture
def budget(monkeypatch):
    monkeypatch.setattr(catalog, "_index_key", lambda: b"x" * 32)
    return budget_for("bitrix24", "synthetic-portal")


class Clock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def __call__(self):
        return self.value

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.value += delay


class FakeStore:
    def __init__(self, replies=None):
        self.replies = list(replies or [0])
        self.calls = []
        self.blocks = []

    async def try_acquire(self, budget, cost):
        self.calls.append((budget, cost))
        return self.replies.pop(0) if self.replies else 0

    async def block(self, budget, delay):
        self.blocks.append((budget, delay))


def limiter(fake, clock):
    return RateLimiter(store=fake, clock=clock, sleep=clock.sleep)


def test_budget_catalog_identity_is_stable_private_and_provider_scoped(monkeypatch, budget):
    monkeypatch.setattr(catalog, "_index_key", lambda: b"x" * 32)
    same = budget_for("bitrix24", "synthetic-portal")
    other = budget_for("caldav", "synthetic-portal")
    assert budget == same and budget.account_key != other.account_key
    assert "synthetic-portal" not in repr(budget) and len(budget.account_key) == 64
    assert (budget.rate, budget.burst, budget.window) == (2, 50, 1)
    assert POLICIES["onec"].rate == 10 and POLICIES["onec"].window == 60
    with pytest.raises(TypeError):
        POLICIES["new"] = 1


@pytest.mark.parametrize("provider,account", [("missing", "a"), ("onec", ""), ("onec", "x\n"), ("onec", "x" * 1025)])
def test_invalid_identity_rejected(monkeypatch, provider, account):
    monkeypatch.setattr(catalog, "_index_key", lambda: b"x" * 32)
    with pytest.raises(ValueError):
        budget_for(provider, account)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: replace(b, rate=200),
        lambda b: replace(b, account_key="not-a-digest"),
        lambda b: replace(b, provider="bad"),
    ],
)
def test_budget_cannot_override_catalog(budget, mutate):
    with pytest.raises(ValueError):
        catalog.validate_budget(mutate(budget))


@pytest.mark.parametrize("cost,wait", [(0, 1), (True, 1), (51, 1), (1, -1), (1, float("nan")), (1, 3601)])
@pytest.mark.asyncio
async def test_invalid_admission_fails_before_store(budget, cost, wait):
    fake, clock = FakeStore(), Clock()
    with pytest.raises(ValueError):
        async with limiter(fake, clock).acquire(budget, cost=cost, max_wait=wait):
            pass
    assert fake.calls == []


@pytest.mark.asyncio
async def test_bounded_wait_rechecks_database_and_spends_without_release(budget):
    fake, clock = FakeStore([0.4, 0.2, 0]), Clock()
    async with limiter(fake, clock).acquire(budget, cost=2, max_wait=1):
        assert len(fake.calls) == 3
    assert clock.sleeps == [0.4, 0.2]
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_long_delay_defers_without_waiting_or_calling_operation(budget):
    fake, clock = FakeStore([3]), Clock()
    with pytest.raises(ProviderDeferred) as exc:
        async with limiter(fake, clock).acquire(budget, max_wait=2):
            pytest.fail("must not dispatch")
    assert exc.value.retry_after == 3 and not clock.sleeps


@pytest.mark.asyncio
async def test_invalid_store_response_fails_closed(budget):
    with pytest.raises(RuntimeError):
        async with limiter(FakeStore([float("nan")]), Clock()).acquire(budget):
            pass


@pytest.mark.parametrize(
    "status,provider,body,expected",
    [
        (429, "caldav", None, True),
        (503, "onec", None, True),
        (503, "caldav", None, False),
        (200, "bitrix24", {"error": "QUERY_LIMIT_EXCEEDED"}, True),
        (200, "bitrix24", {"error": "other"}, False),
    ],
)
def test_normalizes_provider_signals(status, provider, body, expected):
    signal = provider_signal(provider, status, {"retry-after": "12"}, body)
    assert (signal is not None) == expected
    if expected:
        assert signal.retry_after == 12


def test_retry_after_date_and_invalid_values():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert retry_after_seconds("Thu, 01 Jan 2026 00:00:17 GMT", now=now) == 17
    assert retry_after_seconds("Wed, 31 Dec 2025 23:59:59 GMT", now=now) == 0
    for value in (None, "nan", "-1", "inf", "garbage", "Thu, 01 Jan 2026 00:00:17"):
        assert retry_after_seconds(value, now=now) is None
    assert retry_after_seconds("9" * 1000) == 1e300
    assert ProviderLimitSignal(True).retry_after is None


@pytest.mark.asyncio
async def test_read_retries_with_shared_signal_and_bounded_jitter(budget):
    fake, clock = FakeStore([0, 0]), Clock()
    operation = AsyncMock(side_effect=[ProviderLimitSignal(2), "ok"])
    result = await execute_read(limiter(fake, clock), budget, operation, max_wait=5, jitter=lambda a, b: 0.5)
    assert result == "ok" and operation.await_count == 2
    assert fake.blocks == [(budget, 2)] and clock.sleeps == [2]
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_read_defers_if_provider_hint_exceeds_total_budget(budget):
    fake, clock = FakeStore(), Clock()
    operation = AsyncMock(side_effect=ProviderLimitSignal(20))
    with pytest.raises(ProviderDeferred) as exc:
        await execute_read(limiter(fake, clock), budget, operation, max_wait=3)
    assert exc.value.retry_after == 20
    assert operation.await_count == 1 and fake.blocks == [(budget, 20)] and not clock.sleeps


@pytest.mark.asyncio
async def test_write_is_never_retried_and_unknown_is_distinct(budget):
    fake, clock = FakeStore(), Clock()
    operation = AsyncMock(side_effect=ProviderLimitSignal(5))
    with pytest.raises(WriteOutcomeUnknown):
        await execute_write(limiter(fake, clock), budget, operation)
    assert operation.await_count == 1 and fake.blocks == [(budget, 5)]
    operation = AsyncMock(side_effect=TimeoutError("sensitive data"))
    with pytest.raises(WriteOutcomeUnknown) as exc:
        await execute_write(limiter(FakeStore(), clock), budget, operation)
    assert "sensitive" not in str(exc.value) and operation.await_count == 1
    no_send = AsyncMock()
    with pytest.raises(ProviderDeferred):
        await execute_write(limiter(FakeStore([9]), clock), budget, no_send)
    no_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_signal_persistence_failure_still_unknown(budget):
    fake, clock = FakeStore(), Clock()
    fake.block = AsyncMock(side_effect=RuntimeError("private"))
    with pytest.raises(WriteOutcomeUnknown) as exc:
        await execute_write(limiter(fake, clock), budget, AsyncMock(side_effect=ProviderLimitSignal(2)))
    assert "private" not in str(exc.value)


@pytest.mark.asyncio
async def test_success_and_non_limit_read_errors(budget):
    clock = Clock()
    assert await execute_write(limiter(FakeStore(), clock), budget, AsyncMock(return_value=42)) == 42
    with pytest.raises(ValueError):
        await execute_read(limiter(FakeStore(), clock), budget, AsyncMock(side_effect=ValueError("bad")))
    with pytest.raises(ValueError):
        await execute_read(limiter(FakeStore(), clock), budget, AsyncMock(), max_attempts=0)


def test_refill_does_not_count_provider_cooldown_or_negative_clock(budget):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = SimpleNamespace(tokens=0.0, updated_at=now, blocked_until=now + timedelta(seconds=10))
    assert store._refill(row, budget, now + timedelta(seconds=11)) == 2
    assert store._refill(row, budget, now - timedelta(seconds=2)) == 0
    assert store._refill(row, budget, now + timedelta(days=1)) == 50
    assert store._deadline(now, 1e300).year == 9999


class FakeSession:
    def __init__(self, row, now):
        self.row, self.now, self.statements = row, now, []
        self.n = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, stmt):
        self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))

    async def scalar(self, stmt):
        self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))
        self.n += 1
        return self.row if self.n % 2 else self.now


@pytest.mark.asyncio
async def test_postgres_store_uses_atomic_insert_lock_and_database_clock(monkeypatch, budget):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = SimpleNamespace(tokens=3.0, updated_at=now, blocked_until=None)
    session = FakeSession(row, now)
    monkeypatch.setattr(store, "async_session_maker", lambda: session)
    db = store.PostgresBudgetStore()
    assert await db.try_acquire(budget, 2) == 0
    assert row.tokens == 1
    assert "lock_timeout" in session.statements[0]
    assert "statement_timeout" in session.statements[1]
    assert "ON CONFLICT" in session.statements[2]
    assert "FOR UPDATE" in session.statements[3]
    assert "clock_timestamp()" in session.statements[4]
    assert await db.try_acquire(budget, 2) == 0.5
    await db.block(budget, 7)
    assert row.tokens == 0 and row.blocked_until == now + timedelta(seconds=7)
    assert await db.try_acquire(budget, 1) == 7
    await db.block(budget, 2)
    assert row.blocked_until == now + timedelta(seconds=7)


@pytest.mark.asyncio
async def test_lock_contention_defers_or_retries_within_budget(budget):
    fake, clock = FakeStore(), Clock()
    fake.try_acquire = AsyncMock(side_effect=[store.BudgetStoreBusy(), 0])
    async with limiter(fake, clock).acquire(budget, max_wait=1):
        pass
    assert clock.sleeps == [0.25]
    fake.try_acquire = AsyncMock(side_effect=store.BudgetStoreBusy())
    with pytest.raises(ProviderDeferred) as exc:
        async with limiter(fake, clock).acquire(budget, max_wait=0):
            pass
    assert exc.value.retry_after == 0.25


@pytest.mark.asyncio
async def test_db_store_rejects_bad_cost_before_opening_session(monkeypatch, budget):
    sessions = AsyncMock()
    monkeypatch.setattr(store, "async_session_maker", sessions)
    with pytest.raises(ValueError):
        await store.PostgresBudgetStore().try_acquire(budget, float("nan"))
    sessions.assert_not_called()


def test_migration_model_schema_parity_without_applying(monkeypatch):
    import importlib

    from app.core.config import settings
    from app.models import Base
    from app.models.integrations.rate_budget import ProviderRateBudget

    migration = importlib.import_module("migrations.versions.0130_provider_rate_budgets")
    created = []
    dropped = []
    monkeypatch.setattr(
        migration.op, "create_table", lambda name, *args, **kwargs: created.append((name, args, kwargs))
    )
    monkeypatch.setattr(migration.op, "drop_table", lambda name, **kwargs: dropped.append((name, kwargs)))
    migration.upgrade()
    assert created[0][0] == "provider_rate_budgets" and created[0][2]["schema"] == settings.DB_SCHEMA
    assert {column.name for column in created[0][1] if hasattr(column, "type")} == set(
        ProviderRateBudget.__table__.columns.keys()
    )
    assert Base.metadata.tables[f"{settings.DB_SCHEMA}.provider_rate_budgets"] is ProviderRateBudget.__table__
    assert migration.down_revision == "0129"
    migration.downgrade()
    assert dropped == [("provider_rate_budgets", {"schema": settings.DB_SCHEMA})]


@pytest.mark.asyncio
async def test_zero_hint_has_one_second_minimum_cooldown(budget):
    fake, clock = FakeStore(), Clock()
    op = AsyncMock(side_effect=[ProviderLimitSignal(), "ok"])
    assert await execute_read(limiter(fake, clock), budget, op, jitter=lambda a, b: 0, max_wait=2) == "ok"
    assert fake.blocks == [(budget, 1.0)] and clock.sleeps == [1.0]


@pytest.mark.asyncio
async def test_cancellation_after_admission_propagates_without_refunding(budget):
    fake, clock = FakeStore(), Clock()
    operation = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await execute_write(limiter(fake, clock), budget, operation)
    assert operation.await_count == 1 and len(fake.calls) == 1
