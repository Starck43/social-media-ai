"""P21-T14: shared cross-process provider rate budget with bounded backpressure.

A single `RateAcquirer` implements the `dispatch_seam.RateAcquirer` protocol
(seam step 7b): it atomically admits provider capacity across shared
token-bucket scopes (`llm_rate_buckets`) and bounds in-flight attempts with
reclaimable dispatch leases (`llm_dispatch_leases`). Nothing is wired live:
the seam flag stays off and strict mode still fails closed on the missing
P21-T13…P21-T16 foundations, so no provider request is newly admitted by this
module alone.

Design
------
* One row per atomic bucket scope in `llm_rate_buckets`: opaque pool identity
  (`pool:<credential ref>` — the credential **ref**, never its version, so
  rotation never refills the pool), a scope discriminator
  (`upstream`/`holder`/`actor`/`workspace`), a scope key, a unit
  (`request`/`token`) and an optional model family. NULL family folds to ''
  in the unique index so it can never admit duplicate buckets.
* `blocked_until` is a Retry-After floor: refill earns nothing while blocked
  and a huge hint is never shortened. Units are spent at admission and never
  refunded for a possibly sent request.
* `llm_dispatch_leases` bounds in-flight slots; the bounded reclaim query
  `WHERE released_at IS NULL AND expires_at <= now` recovers crashed slots
  (a token bucket alone cannot). Lease expiry never releases quota or token /
  USD liability.
* Lock order (documented for P21-T15's token/USD UOW): dispatch-lease rows
  `(scope, scope_key)` first, then rate buckets `(scope, scope_key, unit)` in
  ascending order — every transaction follows it, so there is no deadlock.
* Fail-closed: store/lock failure denies (`CODE_CAPACITY_UNAVAILABLE` via the
  seam); there is no scheduler, no Redis queue and no unbounded retry — the
  wait loop is bounded by `LLM_RATE_MAX_WAIT_SECONDS`, beyond which the
  attempt is denied.

Classification (kept distinct from admission denial):
429 / 5xx / timeout are transient and back off via `blocked_until`;
auth / validation / budget / authorization / revocation never back off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.database import async_session_maker
from app.models import LLMDispatchLease, LLMRateBucket
from app.services.ai.dispatch_seam import DispatchLease, is_valid_dispatch_id

logger = logging.getLogger(__name__)

MAX_WAIT_DEFAULT = 8.0          # seconds: the bounded wait before a denial
LEASE_TTL_DEFAULT = 60.0        # seconds: an in-flight lease expires this late
LOCK_TIMEOUT_MS_DEFAULT = 250   # mirrors PostgresBudgetStore
RECLAIM_BATCH = 8               # bounded reclaim per acquire attempt

#: PostgreSQL lock-timeout (serialization failure) — fail closed, never retry.
_LOCK_TIMEOUT_SQLSTATE = "55P03"

#: Status codes that only transiently back off; everything else is permanent.
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic() -> float:
    return time.monotonic()


# ── Provider-signal classification ─────────────────────────────────────────


@dataclass(frozen=True)
class ProviderSignal:
    """Classification of one provider outcome; only `transient` backs off."""

    kind: str  # "transient" | "permanent"
    retry_after: float | None = None
    status_code: int | None = None


def classify_provider_signal(
    status_code: int | None = None,
    exc: BaseException | None = None,
    *,
    retry_after_header: float | None = None,
) -> ProviderSignal:
    """Classify a provider outcome without ever stringifying `exc`.

    Transient: 429, 5xx, httpx timeout/transport errors.
    Permanent: auth (401), authorization (403), validation (400), budget (402),
    revocation (signaled through auth/authorization codes) and any unknown
    status — none of these ever back off.
    """
    if exc is not None:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return ProviderSignal("transient", None, None)
        if isinstance(exc, httpx.TransportError):
            return ProviderSignal("transient", None, None)
    if type(status_code) is int:
        if status_code in _TRANSIENT_STATUS_CODES:
            return ProviderSignal("transient", retry_after_header, status_code)
        if status_code in (401, 403):
            return ProviderSignal("permanent", None, status_code)
        if status_code == 400:
            return ProviderSignal("permanent", None, status_code)
        if status_code == 402:
            return ProviderSignal("permanent", None, status_code)
        return ProviderSignal("permanent", None, status_code)
    return ProviderSignal("permanent", None, None)


# ── Outcomes ───────────────────────────────────────────────────────────────


class RateDeferred(Exception):
    """The bucket cannot admit yet; retry after `retry_after` seconds."""

    def __init__(self, retry_after: float):
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(f"rate deferred for {self.retry_after:.3f}s")


class RateDenied(Exception):
    """The bounded wait expired; the attempt is denied, never retried silently."""

    def __init__(self, retry_after: float):
        self.retry_after = max(0.0, float(retry_after))
        super().__init__("rate denied")


class RateStoreUnavailable(Exception):
    """Limiter outage or lock timeout: fail closed."""


# ── Bucket plan ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BucketItem:
    scope: str
    scope_key: str
    unit: str
    cost: float
    capacity: float
    refill_per_sec: float
    model_family: str | None


@dataclass(frozen=True)
class _RatePlan:
    buckets: tuple[BucketItem, ...]
    concurrency_key: str
    concurrency_limit: int
    model_family: str | None


def _family_of(model: Any) -> str | None:
    value = getattr(model, "model_family", None)
    return value if type(value) is str and value else None


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _plan(request: Any, context: Any) -> _RatePlan:
    """One admission attempt's rate plan: shared pool + fairness buckets.

    The upstream pool key is the opaque credential ref: rotation changes the
    credential version, never the ref, so the pool is never refilled by it.
    holder / actor / workspace are separate fairness buckets — none of them is
    the upstream quota pool.
    """
    rpm = float(getattr(settings, "LLM_RATE_RPM", 0) or 0)
    tpm = float(getattr(settings, "LLM_RATE_TPM", 0) or 0)
    concurrency = int(getattr(settings, "LLM_RATE_CONCURRENCY", 0) or 0)
    family = _family_of(request.model)
    ref = getattr(context, "credential_ref", None)
    ref = ref if type(ref) is str and ref else None
    pool_key = f"pool:{ref}" if ref else "global"
    buckets: list[BucketItem] = []
    token_cost = getattr(request, "requested_output_tokens", None)
    token_cost = token_cost if type(token_cost) is int and token_cost > 0 else 0
    if ref:
        if rpm > 0:
            buckets.append(BucketItem("upstream", pool_key, "request", 1.0, rpm, rpm / 60.0, family))
        if tpm > 0 and token_cost > 0:
            buckets.append(BucketItem("upstream", pool_key, "token", float(token_cost), tpm, tpm / 60.0, family))
    holder = getattr(context, "plan_holder_user_id", None)
    actor = getattr(context, "actor_user_id", None)
    tenant = getattr(context, "tenant_id", None)
    for scope, subject in (("holder", holder), ("actor", actor), ("workspace", tenant)):
        if not _positive(subject):
            continue
        key = f"{scope}:{subject}"
        if rpm > 0:
            buckets.append(BucketItem(scope, key, "request", 1.0, rpm, rpm / 60.0, None))
        if tpm > 0 and token_cost > 0:
            buckets.append(BucketItem(scope, key, "token", float(token_cost), tpm, tpm / 60.0, None))
    buckets.sort(key=lambda b: (b.scope, b.scope_key, b.unit))  # lock order
    return _RatePlan(
        buckets=tuple(buckets),
        concurrency_key=pool_key,
        concurrency_limit=concurrency,
        model_family=family,
    )


# ── Store protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class RateStore(Protocol):
    """One atomic admission attempt; implementations keep their own lock order."""

    async def acquire(
        self,
        *,
        plan: _RatePlan,
        dispatch_id: str,
        now: datetime,
        ttl: float,
    ) -> None: ...
    async def release(self, *, dispatch_id: str, now: datetime) -> None: ...
    async def block(self, *, scope: str, scope_key: str, seconds: float) -> None: ...


def _schema_ident() -> str:
    return '"' + settings.DB_SCHEMA + '"'


class PostgresRateStore:
    """PostgreSQL serialization point: SELECT FOR UPDATE on buckets and leases."""

    def __init__(self, lock_timeout_ms: int = LOCK_TIMEOUT_MS_DEFAULT) -> None:
        self.lock_timeout_ms = lock_timeout_ms

    async def acquire(self, *, plan: _RatePlan, dispatch_id: str, now: datetime, ttl: float) -> None:
        schema = _schema_ident()
        expires_at = now + timedelta(seconds=ttl)
        async with async_session_maker() as session, session.begin():
            await session.execute(text(f"SET LOCAL lock_timeout = '{self.lock_timeout_ms}ms'"))
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            # Bounded reclaim of crashed in-flight slots (never refunds units).
            await session.execute(
                text(
                    f"UPDATE {schema}.llm_dispatch_leases SET released_at = :now "
                    f"WHERE released_at IS NULL AND expires_at <= :now LIMIT {RECLAIM_BATCH}"
                ),
                {"now": now},
            )
            # Concurrency guard first: lock in-flight lease rows for the key so
            # concurrent attempts serialize on the same rows (deadlock-free with
            # the bucket order below), then refuse when the limit is reached.
            if plan.concurrency_limit > 0:
                result = await session.execute(
                    text(
                        f"SELECT lease_id FROM {schema}.llm_dispatch_leases "
                        f"WHERE scope = 'upstream' AND scope_key = :key "
                        f"AND released_at IS NULL FOR UPDATE"
                    ),
                    {"key": plan.concurrency_key},
                )
                if result.rowcount is not None and result.rowcount >= plan.concurrency_limit:
                    raise RateDeferred(0.25)
            now = await session.scalar(select(func.clock_timestamp()))
            if now is None:
                raise RateStoreUnavailable("clock unavailable")
            expires_at = now + timedelta(seconds=ttl)
            # Buckets in ascending (scope, scope_key, unit) lock order.
            for bucket in plan.buckets:
                await session.execute(
                    text(
                        f"INSERT INTO {schema}.llm_rate_buckets "
                        f"(scope, scope_key, unit, model_family, capacity_units, "
                        f"refill_units_per_second, available_units, updated_at, blocked_until) "
                        f"VALUES (:scope, :scope_key, :unit, :family, :capacity, "
                        f":refill, :capacity, :now, NULL) "
                        f"ON CONFLICT (scope, scope_key, unit, COALESCE(model_family, '')) DO NOTHING"
                    ),
                    {
                        "scope": bucket.scope,
                        "scope_key": bucket.scope_key,
                        "unit": bucket.unit,
                        "family": bucket.model_family,
                        "capacity": int(bucket.capacity),
                        "refill": bucket.refill_per_sec,
                        "now": now,
                    },
                )
                row = await session.scalar(
                    select(LLMRateBucket).where(
                        LLMRateBucket.scope == bucket.scope,
                        LLMRateBucket.scope_key == bucket.scope_key,
                        LLMRateBucket.unit == bucket.unit,
                        LLMRateBucket.model_family.isnot_distinct_from(bucket.model_family),
                    ).with_for_update()
                )
                if row is None:
                    raise RateStoreUnavailable("bucket unavailable")
                # Retry-After floor: refill earns nothing while blocked.
                since = max(row.updated_at, row.blocked_until) if row.blocked_until else row.updated_at
                elapsed = max(0.0, (now - since).total_seconds())
                available = min(
                    float(bucket.capacity),
                    float(row.available_units or 0) + elapsed * bucket.refill_per_sec,
                )
                if row.blocked_until is not None and now < row.blocked_until:
                    raise RateDeferred((row.blocked_until - now).total_seconds())
                if available < bucket.cost:
                    raise RateDeferred((bucket.cost - available) / bucket.refill_per_sec)
                await session.execute(
                    update(LLMRateBucket)
                    .where(LLMRateBucket.id == row.id)
                    .values(
                        available_units=available - bucket.cost,
                        updated_at=now,
                        blocked_until=None,
                    )
                )
            # One admission lease per attempt; identity is the dispatch id.
            await session.execute(
                pg_insert(LLMDispatchLease)
                .values(
                    lease_id=dispatch_id,
                    scope="upstream",
                    scope_key=plan.concurrency_key,
                    model_family=plan.model_family,
                    expires_at=expires_at,
                    released_at=None,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[LLMDispatchLease.lease_id])
            )
            existing = await session.scalar(
                select(LLMDispatchLease).where(LLMDispatchLease.lease_id == dispatch_id)
            )
            if existing is not None and existing.released_at is None:
                raise RateStoreUnavailable("dispatch identity already in flight")

    async def release(self, *, dispatch_id: str, now: datetime) -> None:
        schema = _schema_ident()
        async with async_session_maker() as session, session.begin():
            await session.execute(
                text(
                    f'UPDATE {schema}.llm_dispatch_leases SET released_at = :now '
                    f"WHERE lease_id = :id AND released_at IS NULL"
                ),
                {"now": now, "id": dispatch_id},
            )

    async def block(self, *, scope: str, scope_key: str, seconds: float) -> None:
        """Retry-After floor: never shorten an existing `blocked_until`."""
        schema = _schema_ident()
        async with async_session_maker() as session, session.begin():
            await session.execute(text(f"SET LOCAL lock_timeout = '{self.lock_timeout_ms}ms'"))
            now = await session.scalar(select(func.clock_timestamp()))
            if now is None:
                raise RateStoreUnavailable("clock unavailable")
            deadline = now + timedelta(seconds=seconds)
            await session.execute(
                text(
                    f"INSERT INTO {schema}.llm_rate_buckets "
                    f"(scope, scope_key, unit, model_family, capacity_units, "
                    f"refill_units_per_second, available_units, updated_at, blocked_until) "
                    f"VALUES (:scope, :scope_key, 'request', NULL, 1, 1.0, 0, :now, :deadline) "
                    f"ON CONFLICT (scope, scope_key, unit, COALESCE(model_family, '')) DO NOTHING"
                ),
                {"scope": scope, "scope_key": scope_key, "now": deadline, "deadline": deadline},
            )
            await session.execute(
                update(LLMRateBucket)
                .where(
                    LLMRateBucket.scope == scope,
                    LLMRateBucket.scope_key == scope_key,
                    LLMRateBucket.unit == "request",
                    LLMRateBucket.model_family.isnot_distinct_from(None),
                )
                .values(
                    available_units=0,
                    updated_at=func.greatest(LLMRateBucket.updated_at, bindparam("now")),
                    blocked_until=func.greatest(LLMRateBucket.blocked_until, bindparam("deadline")),
                ),
                {"now": deadline, "deadline": deadline},
            )


# ── Rate acquirer ──────────────────────────────────────────────────────────


class RateAcquirer:
    """Bounded admission for one provider attempt (dispatch_seam step 7b).

    `store` may be substituted in tests (a fake store is the mocked load check).
    """

    def __init__(
        self,
        *,
        store: RateStore | None = None,
        clock=_now_utc,
        sleep=asyncio.sleep,
        monotonic=_monotonic,
        max_wait: float | None = None,
        lease_ttl: float | None = None,
    ) -> None:
        self.store = store if store is not None else PostgresRateStore(
            lock_timeout_ms=int(getattr(settings, "LLM_RATE_LOCK_TIMEOUT_MS", LOCK_TIMEOUT_MS_DEFAULT) or 0)
        )
        self.clock = clock
        self.sleep = sleep
        self.monotonic = monotonic
        self.max_wait = float(
            max_wait if max_wait is not None else getattr(settings, "LLM_RATE_MAX_WAIT_SECONDS", MAX_WAIT_DEFAULT)
        )
        self.lease_ttl = float(
            lease_ttl if lease_ttl is not None else getattr(settings, "LLM_RATE_LEASE_TTL_SECONDS", LEASE_TTL_DEFAULT)
        )

    async def acquire(self, request: Any, context: Any) -> DispatchLease:
        plan = _plan(request, context)
        dispatch_id = getattr(request, "dispatch_id", None)
        if not is_valid_dispatch_id(dispatch_id):
            raise RateDenied(0.0)
        deadline = self.monotonic() + self.max_wait
        last_retry = 0.0
        while True:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise RateDenied(last_retry)
            try:
                await self.store.acquire(
                    plan=plan,
                    dispatch_id=dispatch_id,
                    now=self.clock(),
                    ttl=self.lease_ttl,
                )
                return DispatchLease(lease_id=dispatch_id)
            except RateDeferred as deferred:
                last_retry = deferred.retry_after
                wait = min(deferred.retry_after, remaining)
                if wait <= 0:
                    raise RateDenied(deferred.retry_after) from None
                try:
                    await self.sleep(wait)
                except asyncio.CancelledError:
                    raise RateDenied(deferred.retry_after) from None
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == _LOCK_TIMEOUT_SQLSTATE:
                    raise RateStoreUnavailable("lock timeout") from None
                raise RateStoreUnavailable("store unavailable") from None

    async def release(self, admission: Any) -> None:
        """Release the admission slot; units are never refunded (no live send
        can be proven not to have happened). Never raises on the caller."""
        lease_id = getattr(getattr(admission, "rate_lease", None), "lease_id", None)
        if not lease_id:
            lease_id = getattr(admission, "dispatch_id", None)
        if not is_valid_dispatch_id(lease_id):
            return
        try:
            await self.store.release(dispatch_id=lease_id, now=self.clock())
        except asyncio.CancelledError:
            raise
        except Exception:
            # A release failure must never fail a settled attempt; the lease
            # expires on its own and the bounded reclaim reuses the slot.
            logger.warning("llm_dispatch_rate_release_failed lease_id=%s", lease_id)

    async def note_signal(self, signal: ProviderSignal, *, scope: str, scope_key: str) -> None:
        """Honor a provider throttle: only transient signals block the bucket.

        `blocked_until` is a floor, so a large Retry-After is never shortened.
        """
        if signal.kind != "transient":
            return
        seconds = signal.retry_after if type(signal.retry_after) in (int, float) else 0.0
        if seconds <= 0:
            seconds = 1.0
        try:
            await self.store.block(scope=scope, scope_key=scope_key, seconds=seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("llm_dispatch_rate_block_failed scope=%s", scope)



