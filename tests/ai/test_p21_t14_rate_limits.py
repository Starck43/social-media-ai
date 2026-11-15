"""P21-T14 mocked checks: rate buckets, backpressure and leases.

No database: a fake store exercises `RateAcquirer` at hundreds of concurrent
admissions. DB upgrade/downgrade and two-session scenarios are deferred to the
local runner (no DB on the remote lane).
"""

import asyncio
import types

import pytest

from app.services.ai.dispatch_seam import (
    DispatchAdmission,
    DispatchContext,
    DispatchLease,
    DispatchRequest,
)
from app.services.ai.rate_limits import (
    MAX_WAIT_DEFAULT,
    BucketItem,
    ProviderSignal,
    RateAcquirer,
    RateDeferred,
    RateDenied,
    RateStoreUnavailable,
    classify_provider_signal,
)

OWNER = 11
TENANT = 21


def make_request(dispatch_id="a" * 32, requested_output_tokens=None, model_family=None):
    model = types.SimpleNamespace(model_family=model_family)
    return DispatchRequest(
        model=model,
        purpose="chat",
        stage="chat",
        dispatch_id=dispatch_id,
        requested_output_tokens=requested_output_tokens,
    )


def make_context(**changes):
    base = dict(
        actor_user_id=OWNER,
        tenant_id=TENANT,
        plan_holder_user_id=OWNER,
        credential_owner_user_id=OWNER,
        credential_ref="cred-abcd",
    )
    base.update(changes)
    return DispatchContext(**base)


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def make_sleep(clock):
    async def _sleep(seconds):
        clock.now += seconds

    return _sleep


class FakeBucket:
    __slots__ = ("capacity", "refill", "available", "updated", "blocked_until")

    def __init__(self, capacity, refill, now):
        self.capacity = float(capacity)
        self.refill = float(refill)
        self.available = float(capacity)
        self.updated = now
        self.blocked_until = None


class FakeRateStore:
    """In-memory mirror of the Postgres store semantics for mocked tests."""

    def __init__(self, rpm=60.0, tpm=1000.0, concurrency=10, now=0.0):
        self.rpm = rpm
        self.tpm = tpm
        self.concurrency = concurrency
        self.now = now
        self.buckets = {}
        self.leases = {}
        self.block_calls = []
        self.max_observed_in_flight = 0
        self.acquires = 0

    async def acquire(self, *, plan, dispatch_id, now, ttl):
        self.acquires += 1
        self.now = now
        # bounded reclaim of crashed slots
        for lease in self.leases.values():
            if lease["released_at"] is None and lease["expires_at"] <= self.now:
                lease["released_at"] = self.now
        if self.concurrency > 0:
            inflight = sum(
                1
                for lease in self.leases.values()
                if lease["scope_key"] == plan.concurrency_key and lease["released_at"] is None
            )
            if inflight >= self.concurrency:
                raise RateDeferred(0.25)
        for bucket in plan.buckets:
            key = (bucket.scope, bucket.scope_key, bucket.unit, bucket.model_family)
            b = self.buckets.get(key)
            if b is None:
                b = FakeBucket(bucket.capacity, bucket.refill_per_sec, self.now)
                self.buckets[key] = b
            since = max(b.updated, b.blocked_until) if b.blocked_until else b.updated
            elapsed = max(0.0, self.now - since)
            available = min(b.capacity, b.available + elapsed * bucket.refill_per_sec)
            if b.blocked_until is not None and self.now < b.blocked_until:
                raise RateDeferred(b.blocked_until - self.now)
            if available < bucket.cost:
                raise RateDeferred((bucket.cost - available) / bucket.refill_per_sec)
            b.available = available - bucket.cost
            b.updated = self.now
            b.blocked_until = None
        if dispatch_id in self.leases and self.leases[dispatch_id]["released_at"] is None:
            raise RateStoreUnavailable("dispatch identity already in flight")
        self.leases[dispatch_id] = {
            "scope": "upstream",
            "scope_key": plan.concurrency_key,
            "released_at": None,
            "expires_at": self.now + ttl,
        }
        inflight = sum(
            1
            for lease in self.leases.values()
            if lease["scope_key"] == plan.concurrency_key and lease["released_at"] is None
        )
        self.max_observed_in_flight = max(self.max_observed_in_flight, inflight)

    async def release(self, *, dispatch_id, now):
        self.now = now
        lease = self.leases.get(dispatch_id)
        if lease is not None and lease["released_at"] is None:
            lease["released_at"] = now

    async def block(self, *, scope, scope_key, seconds):
        deadline = self.now + seconds
        self.block_calls.append((scope, scope_key, deadline))
        key = (scope, scope_key, "request", None)
        b = self.buckets.get(key)
        if b is None:
            b = FakeBucket(1.0, 1.0, self.now)
            self.buckets[key] = b
        b.available = 0.0
        b.blocked_until = max(b.blocked_until or 0.0, deadline)
        b.updated = max(b.updated or 0.0, deadline)


class llm_rate_limits:
    """Temporarily override the rate-limit settings (restored on exit)."""

    def __init__(self, rpm=None, tpm=None, concurrency=None):
        self.rpm = rpm
        self.tpm = tpm
        self.concurrency = concurrency
        from app.core.config import settings

        self.settings = settings
        self.saved = {
            "RPM": settings.LLM_RATE_RPM,
            "TPM": settings.LLM_RATE_TPM,
            "CONCURRENCY": settings.LLM_RATE_CONCURRENCY,
        }

    def __enter__(self):
        from app.core.config import settings

        if self.rpm is not None:
            settings.LLM_RATE_RPM = self.rpm
        if self.tpm is not None:
            settings.LLM_RATE_TPM = self.tpm
        if self.concurrency is not None:
            settings.LLM_RATE_CONCURRENCY = self.concurrency
        return self

    def __exit__(self, *exc):
        self.settings.LLM_RATE_RPM = self.saved["RPM"]
        self.settings.LLM_RATE_TPM = self.saved["TPM"]
        self.settings.LLM_RATE_CONCURRENCY = self.saved["CONCURRENCY"]
        return False


@pytest.mark.parametrize(
    "status,exc,expected",
    [
        (429, None, "transient"),
        (500, None, "transient"),
        (503, None, "transient"),
        (401, None, "permanent"),
        (403, None, "permanent"),
        (400, None, "permanent"),
        (402, None, "permanent"),
        (418, None, "permanent"),
        (None, None, "permanent"),
    ],
)
async def test_classify_provider_signal(status, exc, expected):
    signal = classify_provider_signal(status, exc)
    assert signal.kind == expected


async def test_classify_timeout_and_transport_are_transient():
    import httpx

    assert classify_provider_signal(exc=httpx.TimeoutException("t")).kind == "transient"
    assert classify_provider_signal(exc=httpx.ConnectError("t")).kind == "transient"


async def test_classify_429_carries_retry_after():
    signal = classify_provider_signal(429, retry_after_header=30.0)
    assert signal.kind == "transient"
    assert signal.retry_after == 30.0


async def test_admit_under_capacity_succeeds():
    with llm_rate_limits(rpm=60.0, concurrency=5):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=60.0, concurrency=5, now=1000.0)
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock))
        lease = await acquirer.acquire(make_request(), make_context())
        assert type(lease.lease_id) is str and len(lease.lease_id) == 32
        assert store.acquires == 1


async def test_over_capacity_defers_then_admits():
    with llm_rate_limits(rpm=1.0, concurrency=5):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=1.0, concurrency=5, now=1000.0)  # 1 req/min refill
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock), max_wait=200.0)
        await acquirer.acquire(make_request(), make_context())
        # bounded backpressure: the acquirer retries internally until refill
        clock.now += 61.0
        lease = await acquirer.acquire(make_request("c" * 32), make_context())
        assert lease.lease_id == "c" * 32


async def test_bounded_wait_expires_into_denial():
    with llm_rate_limits(rpm=1.0, concurrency=5):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=1.0, concurrency=5, now=1000.0)
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock), max_wait=0.5)
        await acquirer.acquire(make_request(), make_context())
        with pytest.raises(RateDenied):
            await acquirer.acquire(make_request("b" * 32), make_context())


async def test_retry_after_floor_never_shortens():
    with llm_rate_limits(rpm=60.0, concurrency=5):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=60.0, concurrency=5, now=1000.0)
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock), max_wait=0.1)
        # a provider throttle blocks the bucket for 1000 seconds
        await acquirer.note_signal(ProviderSignal("transient", 1000.0), scope="upstream", scope_key="pool:cred-abcd")
        # the bounded wait expires on the huge floor: denial, retry_after intact
        with pytest.raises(RateDenied) as exc:
            await acquirer.acquire(make_request(), make_context())
        assert exc.value.retry_after > 900.0
        # time passes but the floor holds: still blocked at +500s
        clock.now += 500.0
        with pytest.raises(RateDenied) as exc:
            await acquirer.acquire(make_request("b" * 32), make_context())
        assert exc.value.retry_after > 400.0


async def test_permanent_signals_do_not_block():
    with llm_rate_limits(rpm=60.0, concurrency=5):
        clock = FakeClock(1000.0)
        store = FakeRateStore(now=1000.0)
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock))
        for code in (401, 403, 400, 402, 418):
            await acquirer.note_signal(classify_provider_signal(code), scope="upstream", scope_key="pool:cred-abcd")
        assert store.block_calls == []
        # admission still works (bucket untouched)
        lease = await acquirer.acquire(make_request(), make_context())
        assert lease.lease_id == "a" * 32


async def test_release_frees_slot_without_refunding_units():
    with llm_rate_limits(rpm=1.0, concurrency=2):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=1.0, concurrency=2, now=1000.0)
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock), max_wait=100.0)
        admission = DispatchAdmission(
            dispatch_id="d" * 32,
            context=make_context(),
            request=make_request("d" * 32),
            rate_lease=DispatchLease(lease_id="d" * 32),
        )
        await acquirer.acquire(admission.request, admission.context)
        assert store.leases["d" * 32]["released_at"] is None
        # units spent are not refunded by release
        await acquirer.release(admission)
        assert store.leases["d" * 32]["released_at"] is not None
        bucket = store.buckets[("upstream", "pool:cred-abcd", "request", None)]
        assert bucket.available == 0.0  # not refunded


async def test_fail_closed_on_store_unavailable():
    clock = FakeClock(1000.0)

    class BrokenStore:
        async def acquire(self, *, plan, dispatch_id, now, ttl):
            raise RateStoreUnavailable("down")

        async def release(self, *, dispatch_id, now):
            return None

        async def block(self, *, scope, scope_key, deadline):
            return None

    acquirer = RateAcquirer(store=BrokenStore(), clock=clock, monotonic=clock, sleep=make_sleep(clock))
    with pytest.raises(RateStoreUnavailable):
        await acquirer.acquire(make_request(), make_context())


async def test_hundreds_concurrent_respect_concurrency_limit():
    with llm_rate_limits(rpm=10000.0, tpm=1000000.0, concurrency=4):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=10000.0, tpm=1000000.0, concurrency=4, now=1000.0)
        sleep = make_sleep(clock)
        acquirer = RateAcquirer(
            store=store,
            clock=clock,
            monotonic=clock,
            sleep=sleep,
            max_wait=10000.0,
            lease_ttl=2.0,
        )

        async def worker(n):
            rid = f"{n:04d}" + "0" * 28  # 32 lowercase-hex characters
            request = make_request(dispatch_id=rid)
            lease = await acquirer.acquire(request, make_context())
            assert lease.lease_id == rid
            await sleep(0.1)
            admission = DispatchAdmission(
                dispatch_id=rid,
                context=make_context(),
                request=request,
                rate_lease=DispatchLease(lease_id=rid),
            )
            await acquirer.release(admission)

        await asyncio.gather(*(worker(n) for n in range(300)))
        assert store.acquires == 300
        assert store.max_observed_in_flight <= 4


async def test_credential_rotation_does_not_refill_pool():
    """The pool key is the opaque ref; rotation changes version, never refill."""
    with llm_rate_limits(rpm=1.0, concurrency=5):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=1.0, concurrency=5, now=1000.0)
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock), max_wait=0.1)
        ctx_v1 = make_context(credential_ref="cred-abcd", credential_version=1)
        await acquirer.acquire(make_request(), ctx_v1)
        # same ref, rotated version: same pool, still empty → bounded denial
        ctx_v2 = make_context(credential_ref="cred-abcd", credential_version=9)
        with pytest.raises(RateDenied):
            await acquirer.acquire(make_request("b" * 32), ctx_v2)


async def test_token_bucket_charges_requested_output_tokens():
    with llm_rate_limits(rpm=1000.0, tpm=100.0, concurrency=5):
        clock = FakeClock(1000.0)
        store = FakeRateStore(rpm=1000.0, tpm=100.0, concurrency=5, now=1000.0)
        acquirer = RateAcquirer(store=store, clock=clock, monotonic=clock, sleep=make_sleep(clock), max_wait=0.1)
        await acquirer.acquire(make_request(requested_output_tokens=50), make_context())
        bucket = store.buckets[("upstream", "pool:cred-abcd", "token", None)]
        assert bucket.available == 50.0
        # a larger request must wait for the token refill: bounded denial
        with pytest.raises(RateDenied):
            await acquirer.acquire(make_request("b" * 32, requested_output_tokens=60), make_context())


async def test_plan_uses_settings_defaults_pass_through():
    from app.core.config import settings

    saved = {
        "RPM": settings.LLM_RATE_RPM,
        "TPM": settings.LLM_RATE_TPM,
        "CONCURRENCY": settings.LLM_RATE_CONCURRENCY,
    }
    try:
        settings.LLM_RATE_RPM = 0
        settings.LLM_RATE_TPM = 0
        settings.LLM_RATE_CONCURRENCY = 0
        from app.services.ai.rate_limits import _plan

        plan = _plan(make_request(), make_context())
        assert plan.buckets == ()
        assert plan.concurrency_limit == 0
    finally:
        settings.LLM_RATE_RPM = saved["RPM"]
        settings.LLM_RATE_TPM = saved["TPM"]
        settings.LLM_RATE_CONCURRENCY = saved["CONCURRENCY"]


async def test_acquirer_defaults_come_from_settings():
    from app.core.config import settings

    saved = {
        "MAX_WAIT": settings.LLM_RATE_MAX_WAIT_SECONDS,
        "LEASE_TTL": settings.LLM_RATE_LEASE_TTL_SECONDS,
        "LOCK_TIMEOUT": settings.LLM_RATE_LOCK_TIMEOUT_MS,
    }
    try:
        settings.LLM_RATE_MAX_WAIT_SECONDS = 1.5
        settings.LLM_RATE_LEASE_TTL_SECONDS = 9.0
        settings.LLM_RATE_LOCK_TIMEOUT_MS = 500
        acquirer = RateAcquirer()
        assert acquirer.max_wait == 1.5
        assert acquirer.lease_ttl == 9.0
        assert type(acquirer.store).__name__ == "PostgresRateStore"
    finally:
        settings.LLM_RATE_MAX_WAIT_SECONDS = saved["MAX_WAIT"]
        settings.LLM_RATE_LEASE_TTL_SECONDS = saved["LEASE_TTL"]
        settings.LLM_RATE_LOCK_TIMEOUT_MS = saved["LOCK_TIMEOUT"]


async def test_rate_limits_constants():
    assert MAX_WAIT_DEFAULT > 0
