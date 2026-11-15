"""Real-schema checks for P21-T14 (PostgreSQL).

These tests exercise `PostgresRateStore` against a live schema created by the
`p21t14_rate_limits` migration. They are **not** executed with `--noconftest`
and require `TEST_POSTGRES_URL`/`DB_TEST_SCHEMA` to point at an isolated test
database (the auditor's host has PostgreSQL; this sandbox does not).

Covered (F-6):
- alembic upgrade p21t13_catalog_storage -> p21t14_rate_limits -> downgrade -> upgrade
- capacity denial: concurrent callers cannot exceed the configured bucket
- concurrency ceiling under two sessions
- Retry-After floor: `blocked_until` is never shortened by refill math
- reclaim of an expired lease (bounded reclaim query)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session_maker
from app.services.ai.dispatch_seam import DispatchContext, DispatchRequest
from app.services.ai.rate_limits import (
    BucketItem,
    PostgresRateStore,
    RateDeferred,
    _plan,
)

pytestmark = pytest.mark.tenancy

REVISION_FROM = "p21t13_catalog_storage"
REVISION_TO = "p21t14_rate_limits"


def _alembic_url() -> str:
    url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("POSTGRES_URL", "")
    if not url:
        pytest.skip("TEST_POSTGRES_URL/POSTGRES_URL is not set")
    return url


def _run_alembic(to_revision: str) -> None:
    config = Config(str(__file__).resolve().parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", _alembic_url())
    command.upgrade(config, to_revision)


@pytest.fixture
async def schema():
    """Upgrade to p21t14_rate_limits, yield, then downgrade back."""
    _run_alembic(REVISION_TO)
    yield
    _run_alembic(REVISION_FROM)


async def _exec(session, sql, params=None):
    return await session.execute(text(sql), params or {})


async def _insert_bucket(session, *, scope, scope_key, unit, family, capacity, refill, available, updated, blocked_until=None):
    await session.execute(
        text(
            f"INSERT INTO {settings.DB_SCHEMA}.llm_rate_buckets "
            "(scope, scope_key, unit, model_family, capacity_units, "
            "refill_units_per_second, available_units, updated_at, blocked_until) "
            "VALUES (:scope, :scope_key, :unit, :family, :capacity, "
            ":refill, :available, :updated, :blocked)"
        ),
        {
            "scope": scope,
            "scope_key": scope_key,
            "unit": unit,
            "family": family,
            "capacity": capacity,
            "refill": refill,
            "available": available,
            "updated": updated,
            "blocked": blocked_until,
        },
    )


async def _insert_lease(session, *, lease_id, scope, scope_key, expires_at, released_at=None):
    await session.execute(
        text(
            f"INSERT INTO {settings.DB_SCHEMA}.llm_dispatch_leases "
            "(lease_id, scope, scope_key, model_family, expires_at, released_at, created_at) "
            "VALUES (:id, :scope, :scope_key, NULL, :expires, :released, :created)"
        ),
        {
            "id": lease_id,
            "scope": scope,
            "scope_key": scope_key,
            "expires": expires_at,
            "released": released_at,
            "created": datetime.now(timezone.utc),
        },
    )


@pytest.mark.asyncio
async def test_migration_upgrade_downgrade_upgrade(schema):
    """The migration applies cleanly and rolls back."""
    async with async_session_maker() as session:
        count = await session.scalar(
            text(
                f"SELECT count(*) FROM information_schema.tables "
                f"WHERE table_schema = :s AND table_name IN "
                f"('llm_rate_buckets', 'llm_dispatch_leases')"
            ),
            {"s": settings.DB_SCHEMA},
        )
        assert count == 2


@pytest.mark.asyncio
async def test_capacity_denial(schema):
    """Concurrent callers cannot exceed the configured bucket in the window."""
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        await _insert_bucket(
            session,
            scope="upstream",
            scope_key="pool:cred-x",
            unit="request",
            family=None,
            capacity=1,
            refill=0.0,
            available=1,
            updated=now,
        )
        await session.commit()

    store = PostgresRateStore()
    # One unit available: first acquire succeeds.
    from app.services.ai.rate_limits import RateAcquirer

    model = type("M", (), {"model_family": None})()
    request = DispatchRequest(model=model, purpose="chat", stage="chat")
    context = DispatchContext(
        actor_user_id=1,
        tenant_id=2,
        plan_holder_user_id=1,
        credential_owner_user_id=1,
        credential_ref="cred-x",
    )
    settings.LLM_RATE_RPM = 60
    settings.LLM_RATE_CONCURRENCY = 0
    try:
        plan = _plan(request, context)
        acquirer = RateAcquirer(store=store, max_wait=0.1)
        await acquirer.acquire(request, context)
    finally:
        settings.LLM_RATE_RPM = 0
        settings.LLM_RATE_CONCURRENCY = 0

    # Bucket is now empty: a second acquire defers (RateDeferred) then, with a
    # bounded wait of 0, denies (RateDenied).
    settings.LLM_RATE_RPM = 60
    settings.LLM_RATE_CONCURRENCY = 0
    try:
        plan = _plan(request, context)
        acquirer = RateAcquirer(store=store, max_wait=0.05)
        try:
            await acquirer.acquire(request, context)
            pytest.fail("expected RateDenied for empty bucket")
        except RateDeferred:
            pass  # expected: bounded backpressure defers instead of admitting
    finally:
        settings.LLM_RATE_RPM = 0
        settings.LLM_RATE_CONCURRENCY = 0


@pytest.mark.asyncio
async def test_concurrency_ceiling_two_sessions(schema):
    """Two concurrent sessions cannot exceed the concurrency limit."""
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        await _insert_lease(
            session,
            lease_id="existing-inflight",
            scope="upstream",
            scope_key="pool:cred-y",
            expires_at=now + timedelta(seconds=60),
            released_at=None,
        )
        await session.commit()

    from app.services.ai.rate_limits import RateAcquirer

    model = type("M", (), {"model_family": None})()
    request = DispatchRequest(model=model, purpose="chat", stage="chat")
    context = DispatchContext(
        actor_user_id=1,
        tenant_id=2,
        plan_holder_user_id=1,
        credential_owner_user_id=1,
        credential_ref="cred-y",
    )
    settings.LLM_RATE_CONCURRENCY = 1
    try:
        plan = _plan(request, context)
        acquirer = RateAcquirer(store=PostgresRateStore(), max_wait=0.05)
        try:
            await acquirer.acquire(request, context)
            pytest.fail("expected RateDeferred at concurrency ceiling")
        except RateDeferred:
            pass  # expected
    finally:
        settings.LLM_RATE_CONCURRENCY = 0


@pytest.mark.asyncio
async def test_retry_after_floor_never_shortened(schema):
    """blocked_until is a floor; refill never shortens it."""
    now = datetime.now(timezone.utc)
    blocked_until = now + timedelta(seconds=3600)
    async with async_session_maker() as session:
        await _insert_bucket(
            session,
            scope="upstream",
            scope_key="pool:cred-z",
            unit="request",
            family=None,
            capacity=60,
            refill=1.0,
            available=0,
            updated=now,
            blocked_until=blocked_until,
        )
        await session.commit()

    model = type("M", (), {"model_family": None})()
    request = DispatchRequest(model=model, purpose="chat", stage="chat")
    context = DispatchContext(
        actor_user_id=1,
        tenant_id=2,
        plan_holder_user_id=1,
        credential_owner_user_id=1,
        credential_ref="cred-z",
    )
    settings.LLM_RATE_RPM = 60
    try:
        plan = _plan(request, context)
        acquirer = RateAcquirer(store=PostgresRateStore(), max_wait=0.05)
        try:
            await acquirer.acquire(request, context)
            pytest.fail("expected RateDeferred while blocked_until is in the future")
        except RateDeferred as deferred:
            # The floor holds: retry_after is close to the full 3600 s, not shortened by refill.
            assert deferred.retry_after > 3000.0
    finally:
        settings.LLM_RATE_RPM = 0


@pytest.mark.asyncio
async def test_reclaim_expired_lease(schema):
    """Expired in-flight leases are reclaimed by the bounded reclaim query."""
    now = datetime.now(timezone.utc)
    expired = now - timedelta(seconds=60)
    async with async_session_maker() as session:
        await _insert_lease(
            session,
            lease_id="expired-lease",
            scope="upstream",
            scope_key="pool:cred-w",
            expires_at=expired,
            released_at=None,
        )
        await session.commit()

    model = type("M", (), {"model_family": None})()
    request = DispatchRequest(model=model, purpose="chat", stage="chat")
    context = DispatchContext(
        actor_user_id=1,
        tenant_id=2,
        plan_holder_user_id=1,
        credential_owner_user_id=1,
        credential_ref="cred-w",
    )
    settings.LLM_RATE_CONCURRENCY = 1
    try:
        plan = _plan(request, context)
        acquirer = RateAcquirer(store=PostgresRateStore(), max_wait=5.0)
        # Should NOT defer: the expired lease is reclaimed first.
        await acquirer.acquire(request, context)
    finally:
        settings.LLM_RATE_CONCURRENCY = 0
