"""Retry reads only. A dispatched write is never automatically retried."""

import random

from app.integrations.ratelimit.limiter import ProviderDeferred, _valid_number
from app.integrations.ratelimit.signals import ProviderLimitSignal


class WriteOutcomeUnknown(Exception):
    """A dispatched write did not return a confirmed outcome; reconcile it."""


async def execute_read(limiter, budget, operation, *, max_attempts=3, max_wait=5.0, jitter=None):
    if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise ValueError("Invalid retry count")
    if not _valid_number(max_wait) or max_wait > 3600:
        raise ValueError("Invalid retry wait budget")
    rng = jitter if jitter is not None else random.uniform
    deadline = limiter.clock() + max_wait
    for attempt in range(max_attempts):
        remaining = max(0.0, deadline - limiter.clock())
        async with limiter.acquire(budget, max_wait=remaining):
            try:
                return await operation()
            except ProviderLimitSignal as signal:
                await limiter.note_signal(budget, signal)
                pause = max(1.0, signal.retry_after or 0.0, rng(0.0, min(30.0, 2.0**attempt)))
                if attempt + 1 == max_attempts or pause > max(0.0, deadline - limiter.clock()):
                    raise ProviderDeferred(pause) from None
                if pause:
                    await limiter.sleep(pause)
    raise AssertionError("unreachable")


async def execute_write(limiter, budget, operation, *, max_wait=0):
    # A deferral before admission means nothing was sent and is not unknown.
    async with limiter.acquire(budget, max_wait=max_wait):
        try:
            return await operation()
        except ProviderLimitSignal as signal:
            try:
                await limiter.note_signal(budget, signal)
            except Exception:
                raise WriteOutcomeUnknown("Write outcome unknown; rate-limit state unavailable") from None
            raise WriteOutcomeUnknown("Write outcome unknown; reconcile before retry") from None
        except Exception:
            raise WriteOutcomeUnknown("Write outcome unknown; reconcile before retry") from None
