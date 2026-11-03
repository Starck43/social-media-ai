"""Bounded admission and honest deferral; every admission uses the shared store."""

import asyncio
import math
import time
from contextlib import asynccontextmanager

from app.integrations.ratelimit.catalog import validate_budget
from app.integrations.ratelimit.store import BudgetStoreBusy, PostgresBudgetStore


class ProviderDeferred(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__("Provider request deferred")


def _valid_number(value, *, minimum=0.0):
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


class RateLimiter:
    def __init__(self, *, store=None, clock=None, sleep=None):
        self.store = store if store is not None else PostgresBudgetStore()
        self.clock = clock if clock is not None else time.monotonic
        self.sleep = sleep if sleep is not None else asyncio.sleep

    @asynccontextmanager
    async def acquire(self, budget, *, cost=1, max_wait=0):
        validate_budget(budget)
        if not _valid_number(cost, minimum=0.000001) or cost > budget.burst:
            raise ValueError("Invalid request cost")
        if not _valid_number(max_wait) or max_wait > 3600:
            raise ValueError("Invalid wait budget")
        deadline = self.clock() + max_wait
        while True:
            try:
                delay = await self.store.try_acquire(budget, cost)
            except BudgetStoreBusy:
                delay = 0.25
            if not _valid_number(delay):
                raise RuntimeError("Invalid budget store response")
            if delay == 0:
                break
            if delay > max(0.0, deadline - self.clock()):
                raise ProviderDeferred(delay)
            await self.sleep(delay)
        # A token is spent at admission, not returned on exit: the remote
        # request may have been sent even if the task is cancelled.
        yield

    async def note_signal(self, budget, signal):
        # Unknown/zero hints still impose a small conservative shared cooldown.
        seconds = max(1.0, signal.retry_after or 0.0)
        await self.store.block(budget, seconds)
