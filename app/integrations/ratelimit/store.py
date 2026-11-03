"""PostgreSQL serialization point for all API and runtime processes."""

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from app.core.database import async_session_maker
from app.integrations.ratelimit.catalog import validate_budget
from app.models.integrations.rate_budget import ProviderRateBudget


def _refill(row, budget, now):
    # A provider cooldown is a floor, not time that earns extra tokens.
    since = max(row.updated_at, row.blocked_until) if row.blocked_until else row.updated_at
    elapsed = max(0.0, (now - since).total_seconds())
    return min(float(budget.burst), max(0.0, row.tokens) + elapsed * budget.rate / budget.window)


def _deadline(now, seconds):
    latest = datetime.max.replace(tzinfo=timezone.utc)
    # A huge valid Retry-After must never be shortened to a few seconds.
    return latest if seconds >= (latest - now).total_seconds() else now + timedelta(seconds=seconds)


class BudgetStoreBusy(Exception):
    """A competing transaction held the shared bucket beyond the lock timeout."""


class PostgresBudgetStore:
    """Internal manager; no process-local path may admit without this check."""

    async def _locked(self, session, budget):
        await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
        await session.execute(text("SET LOCAL statement_timeout = '2s'"))
        # The unique key serializes first creation; ON CONFLICT waits for an
        # uncommitted competing insert before SELECT FOR UPDATE acquires it.
        statement = (
            insert(ProviderRateBudget)
            .values(
                provider=budget.provider,
                account_key=budget.account_key,
                tokens=float(budget.burst),
                updated_at=func.clock_timestamp(),
                blocked_until=None,
            )
            .on_conflict_do_nothing(index_elements=["provider", "account_key"])
        )
        await session.execute(statement)
        row = await session.scalar(
            select(ProviderRateBudget)
            .where(ProviderRateBudget.provider == budget.provider, ProviderRateBudget.account_key == budget.account_key)
            .with_for_update()
        )
        # Read server wall clock *after* taking the lock: transaction-start
        # timestamps would be stale after waiting for a competing request.
        now = await session.scalar(select(func.clock_timestamp()))
        if row is None or now is None:
            raise RuntimeError("Provider budget unavailable")
        return row, now

    async def try_acquire(self, budget, cost):
        validate_budget(budget)
        if type(cost) not in (int, float) or not math.isfinite(cost) or not 0 < cost <= budget.burst:
            raise ValueError("Invalid request cost")
        try:
            async with async_session_maker() as session, session.begin():
                row, now = await self._locked(session, budget)
                if row.blocked_until and now < row.blocked_until:
                    return (row.blocked_until - now).total_seconds()
                tokens = _refill(row, budget, now)
                row.tokens = tokens
                row.updated_at = max(now, row.updated_at)
                row.blocked_until = None
                if tokens >= cost:
                    row.tokens = tokens - cost
                    return 0.0
                return (cost - tokens) * budget.window / budget.rate
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise BudgetStoreBusy() from None
            raise

    async def block(self, budget, seconds):
        validate_budget(budget)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid provider cooldown")
        async with async_session_maker() as session, session.begin():
            row, now = await self._locked(session, budget)
            deadline = _deadline(now, seconds)
            row.tokens = 0.0
            row.updated_at = max(now, row.updated_at)
            row.blocked_until = max(row.blocked_until, deadline) if row.blocked_until else deadline
