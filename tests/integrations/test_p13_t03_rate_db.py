"""LOCAL ONLY: real PostgreSQL; root conftest and exclusive test schema required.

Do not run with --noconftest. The migration must be separately approved and
applied to the isolated schema before running. No external provider calls.
"""

import asyncio
import math
import time
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.core.database import async_session_maker
from app.integrations.ratelimit import ProviderDeferred, RateLimiter, budget_for, catalog
from app.integrations.ratelimit.store import PostgresBudgetStore
from app.models.integrations.rate_budget import ProviderRateBudget


@pytest.fixture
async def budget(monkeypatch):
    monkeypatch.setattr(catalog, "_index_key", lambda: b"synthetic-test-key-only-32-bytes!")
    b = budget_for("onec", "synthetic-account-" + uuid4().hex)
    try:
        yield b
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(
                delete(ProviderRateBudget).where(
                    ProviderRateBudget.provider == b.provider, ProviderRateBudget.account_key == b.account_key
                )
            )
        async with async_session_maker() as session:
            remaining = await session.scalar(
                select(func.count())
                .select_from(ProviderRateBudget)
                .where(ProviderRateBudget.provider == b.provider, ProviderRateBudget.account_key == b.account_key)
            )
            assert remaining == 0


@pytest.mark.asyncio
async def test_two_managers_share_budget_under_concurrent_sessions(budget):
    limiters = [RateLimiter(store=PostgresBudgetStore()), RateLimiter(store=PostgresBudgetStore())]
    start = time.monotonic()

    async def attempt(i):
        try:
            async with limiters[i % 2].acquire(budget, max_wait=0):
                return True
        except ProviderDeferred:
            return False

    admitted = await asyncio.gather(*(attempt(i) for i in range(40)))
    elapsed = time.monotonic() - start
    # A token bucket may accrue tokens during the test; include elapsed refill
    # in the upper bound instead of assuming the concurrent run is instantaneous.
    assert sum(admitted) <= budget.burst + math.floor(elapsed * budget.rate / budget.window + 1e-6)
    assert sum(admitted) > 0
    async with async_session_maker() as session:
        rows = (
            await session.scalars(
                select(ProviderRateBudget).where(
                    ProviderRateBudget.provider == budget.provider, ProviderRateBudget.account_key == budget.account_key
                )
            )
        ).all()
        assert len(rows) == 1
        assert 0 <= rows[0].tokens <= budget.burst


@pytest.mark.asyncio
async def test_provider_cooldown_is_visible_to_other_store(budget):
    first, second = PostgresBudgetStore(), PostgresBudgetStore()
    await first.block(budget, 20)
    delay = await second.try_acquire(budget, 1)
    assert 0 < delay <= 20
    # A later shorter provider hint cannot shorten the existing cooldown.
    await second.block(budget, 1)
    assert await first.try_acquire(budget, 1) > 1
