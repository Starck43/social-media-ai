"""LOCAL ONLY: PostgreSQL with root conftest and exclusive test schema.

Do not run with --noconftest. Requires reviewed revisions 0130 and 0131 on the
isolated schema, ownership/inventory/cleanup and no provider calls.
"""

import asyncio
import math
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, inspect, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.ratelimit import ProviderDeferred, RateLimiter, budget_for, catalog
from app.integrations.ratelimit.store import BudgetConnectionUnavailable, PostgresBudgetStore
from app.models import WorkspaceConnection
from app.models.integrations.rate_budget import ProviderRateBudget
from app.models.managers.workspace_connection_manager import workspace_connections
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401


@pytest.fixture
async def world(personal_world, monkeypatch):
    monkeypatch.setattr(catalog, "_key", lambda: b"synthetic-test-key-only-32-bytes!")
    tenants = [t.id for t in personal_world.tenants]
    actor = personal_world.users[0]
    tag = uuid4().hex
    connections = []
    try:
        async with async_session_maker() as session, session.begin():
            for tenant_id in tenants:
                row = WorkspaceConnection(
                    tenant_id=tenant_id,
                    owner_user_id=actor.id,
                    provider_id="onec",
                    platform="onec",
                    auth_method="synthetic",
                    schema_version=1,
                    label="synthetic",
                    account_key="synthetic-" + tag,
                    config={},
                    is_active=True,
                    state="unverified",
                )
                session.add(row)
                await session.flush()
                connections.append(row.id)
        budgets = []
        for tenant_id, connection_id in zip(tenants, connections):
            with tenant_scope(tenant_id):
                budgets.append(budget_for("onec", "synthetic-same-account-" + tag, connection_id=connection_id))
        yield SimpleNamespace(tenants=tenants, actor=actor, connections=connections, budgets=budgets)
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(WorkspaceConnection).where(WorkspaceConnection.id.in_(connections)))
        async with async_session_maker() as session:
            remaining = await session.scalar(
                select(func.count())
                .select_from(ProviderRateBudget)
                .where(ProviderRateBudget.tenant_id.in_(tenants), ProviderRateBudget.connection_id.in_(connections))
            )
            assert remaining == 0


@pytest.mark.asyncio
@pytest.mark.tenancy
async def test_two_managers_share_one_tenant_budget_under_concurrent_sessions(world):
    budget = world.budgets[0]
    limiters = [RateLimiter(store=PostgresBudgetStore()), RateLimiter(store=PostgresBudgetStore())]
    start = time.monotonic()

    async def attempt(i):
        with tenant_scope(world.tenants[0]):
            try:
                async with limiters[i % 2].acquire(budget, max_wait=0):
                    return True
            except ProviderDeferred:
                return False

    admitted = await asyncio.gather(*(attempt(i) for i in range(40)))
    elapsed = time.monotonic() - start
    assert sum(admitted) <= budget.burst + math.floor(elapsed * budget.rate / budget.window + 1e-6)
    assert sum(admitted) > 0
    async with async_session_maker() as session:
        rows = (
            await session.scalars(
                select(ProviderRateBudget).where(
                    ProviderRateBudget.tenant_id == budget.tenant_id,
                    ProviderRateBudget.provider == budget.provider,
                    ProviderRateBudget.account_key == budget.account_key,
                )
            )
        ).all()
        assert len(rows) == 1 and 0 <= rows[0].tokens <= budget.burst


@pytest.mark.asyncio
@pytest.mark.tenancy
async def test_cooldown_and_revocation_do_not_cross_tenants(world):
    a, b = world.budgets
    first, second = PostgresBudgetStore(), PostgresBudgetStore()
    with tenant_scope(world.tenants[0]):
        await first.block(a, 20)
        assert 0 < await second.try_acquire(a, 1) <= 20
    with tenant_scope(world.tenants[1]):
        assert await second.try_acquire(b, 1) == 0


@pytest.mark.asyncio
@pytest.mark.tenancy
async def test_connection_fk_cascades_only_its_tenant_budget(world):
    a, b = world.budgets
    store = PostgresBudgetStore()
    for budget in (a, b):
        with tenant_scope(budget.tenant_id):
            assert await store.try_acquire(budget, 1) == 0

    async with async_session_maker() as session, session.begin():
        connection = await session.connection()
        fks = await connection.run_sync(
            lambda conn: inspect(conn).get_foreign_keys("provider_rate_budgets", schema=settings.DB_SCHEMA)
        )
        pairs = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"])): fk for fk in fks
        }
        for key in (
            (("tenant_id",), "tenants", ("id",)),
            (("tenant_id", "connection_id"), "workspace_connections", ("tenant_id", "id")),
        ):
            assert pairs[key]["referred_schema"] == settings.DB_SCHEMA
            assert pairs[key]["options"]["ondelete"].upper() == "CASCADE"
        await session.execute(
            delete(WorkspaceConnection).where(
                WorkspaceConnection.tenant_id == a.tenant_id, WorkspaceConnection.id == a.connection_id
            )
        )

    async with async_session_maker() as session:
        assert await session.get(ProviderRateBudget, (a.tenant_id, a.provider, a.account_key)) is None
        assert await session.get(ProviderRateBudget, (b.tenant_id, b.provider, b.account_key)) is not None
    with tenant_scope(world.tenants[0]):
        with pytest.raises(BudgetConnectionUnavailable):
            await store.try_acquire(a, 1)
