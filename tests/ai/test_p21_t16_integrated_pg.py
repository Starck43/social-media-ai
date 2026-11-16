"""Strict mocked client route over real T13–T16 services in an owned schema."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import (
    LLMBudgetDay,
    LLMDispatchLease,
    LLMDispatchReservationEvent,
    LLMDispatchReservationState,
    LLMOutcomeEvent,
    LLMTokenAllowanceCycle,
    PlatformRole,
    Tenant,
    User,
)
from app.services.ai import atomic_admission, catalog_grants
from app.services.ai import dispatch_seam as seam
from app.services.ai import llm_client
from app.services.ai.atomic_admission import AtomicLLMAdmissionService
from app.services.ai.catalog_grants import CatalogGrantResolver
from app.services.ai.dispatch_seam import DispatchContext, DispatchFoundation
from app.services.ai.rate_limits import PostgresRateStore, RateAcquirer
from app.services.ai.settlement import LLMSettlementService
from app.services.tenancy.service_plan_policy import (
    TokenAllowancePolicy,
    ValidatedPlanPolicy,
    WorkspacePlanContext,
)

__source_only__ = False


class _GrantResult:
    def __init__(self, mapping=None, scalar_values=()):
        self._mapping = mapping
        self._scalar_values = list(scalar_values)

    def one_or_none(self):
        return SimpleNamespace(_mapping=self._mapping) if self._mapping is not None else None

    def scalars(self):
        return self

    def all(self):
        return list(self._scalar_values)


class _GrantSession:
    """Controlled T13 row/grant projections; authorization itself is real."""

    def __init__(self, model_id, provider_id, catalog_id):
        self.calls = 0
        self.mapping = {
            "id": model_id,
            "catalog_id": catalog_id,
            "model_owner_id": None,
            "capabilities": ["text"],
            "model_is_active": True,
            "provider_id": provider_id,
            "provider_catalog_id": catalog_id,
            "provider_owner_id": None,
            "provider_is_active": True,
            "catalog_row_id": catalog_id,
            "catalog_kind": "platform",
            "catalog_owner_id": None,
            "catalog_is_active": True,
        }

    async def execute(self, _statement):
        self.calls += 1
        if self.calls % 2:
            return _GrantResult(mapping=self.mapping)
        return _GrantResult(scalar_values=("text",))


class _Authority:
    def __init__(self, *, revoked):
        self.revoked = revoked

    async def resolve_identity(self, _request, hints):
        return hints

    async def resolve_credential(self, _request, context):
        return context

    async def resolve_payer(self, _request, context):
        return context

    async def revalidate(self, admission):
        if self.revoked:
            raise seam.DispatchDenial(seam.CODE_PLAN_GRANT_MISSING, "step4_grants")
        return admission.context


async def _seed_payer():
    suffix = uuid.uuid4().hex[:12]
    username = f"p21t16-integrated-{suffix}"
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert role is not None
        user = User(
            username=username,
            email=f"{username}@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        tenant = Tenant(
            name=f"P21-T16 integrated {suffix}",
            slug=f"p21t16-integrated-{suffix}",
            plan="pro",
            plan_holder_user_id=user.id,
            is_active=True,
        )
        session.add(tenant)
        await session.flush()
        session.add(
            LLMBudgetDay(
                tenant_id=tenant.id,
                utc_day=now.date(),
                settled_usd=Decimal(0),
                reserved_usd=Decimal(0),
                undefined_count=0,
                unbounded_count=0,
                frozen=False,
                baseline_version="p21t16-integrated-v1",
            )
        )
        await session.commit()
        return user.id, tenant.id


def _policy_snapshot(actor_id: int, tenant_id: int) -> WorkspacePlanContext:
    policy = ValidatedPlanPolicy(
        plan_code="pro",
        policy_version=5,
        module_grants=(),
        daily_quotas={},
        resource_limits={"model_types": ("text",)},
        owned_workspace_limit_configured=False,
        max_owned_workspaces=None,
        token_policy=TokenAllowancePolicy(unit="token", period="30_days", token_limit=1000, policy_version=5),
    )
    return WorkspacePlanContext(
        actor_user_id=actor_id,
        tenant_id=tenant_id,
        plan_holder_user_id=actor_id,
        plan_code="pro",
        plan_policy_version=5,
        plan_expires_at=None,
        policy=policy,
    )


def _model(provider_id: int):
    provider = SimpleNamespace(
        id=provider_id,
        owner_user_id=None,
        name="Synthetic platform provider",
        api_format="openai",
        base_url="https://provider.invalid",
        auth_header=None,
        credential_version=1,
        get_api_key=lambda: "synthetic-test-key",
    )
    return SimpleNamespace(
        id=provider_id + 1,
        provider=provider,
        owner_user_id=None,
        is_active=True,
        capabilities=["text"],
        name="Synthetic platform model",
        model_id="synthetic-platform-model",
        input_cost_per_1k=Decimal("0.1"),
        output_cost_per_1k=Decimal("0.2"),
        cache_read_cost_per_1m=Decimal(0),
        cache_write_5m_cost_per_1m=Decimal(0),
        cache_write_1h_cost_per_1m=Decimal(0),
        pricing_token_unit=1000,
        context_window_tokens=32,
        max_output_tokens=8,
        max_tokens=8,
        default_temperature=0.1,
        custom_endpoint_path=None,
    )


def _client(model):
    client = object.__new__(llm_client.OpenAICompatibleClient)
    client.model = model
    client.provider = model.provider
    client.api_key = "synthetic-test-key"
    client.credential_version = 1
    client.custom_endpoint_path = None
    client.base_url = model.provider.base_url
    client.model_name = model.model_id
    client.max_tokens = 8
    client.default_temperature = 0.1
    client.timeout = 5.0
    client._rate_limit = AsyncMock()
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_strict_client_route_uses_real_rate_admission_and_settlement(monkeypatch, revoked):
    schema = os.environ.get("P21_T16_ISOLATED_SCHEMA")
    if not schema:
        pytest.skip("P21_T16_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if settings.DB_SCHEMA != schema or schema in {"public", "test_schema"}:
        pytest.fail("Integrated P21-T16 acceptance requires its own isolated schema")

    user_id, tenant_id = await _seed_payer()
    policy = _policy_snapshot(user_id, tenant_id)
    model = _model(8100 + user_id)
    dispatch_id = uuid.uuid4().hex
    monkeypatch.setattr(seam, "uuid4", lambda: SimpleNamespace(hex=dispatch_id))
    context = DispatchContext(
        actor_user_id=user_id,
        tenant_id=tenant_id,
        plan_holder_user_id=user_id,
        credential_owner_user_id=None,
        funding_class="platform",
        payer=f"tenant:{tenant_id}",
        plan_policy_version=policy.plan_policy_version,
        credential_version=1,
        credential_ref=f"p21t16-integrated-{user_id}",
        workspace_plan=policy,
    )

    async def holder_snapshot(*_args, **_kwargs):
        return policy

    monkeypatch.setattr(catalog_grants, "resolve_holder_context", holder_snapshot)
    monkeypatch.setattr(atomic_admission, "resolve_holder_context", holder_snapshot)
    monkeypatch.setattr("app.services.tenancy.resolver.resolved_daily_cost_limit", lambda _tenant: 10.0)
    monkeypatch.setattr(settings, "LLM_DISPATCH_SEAM_ENABLED", True)
    monkeypatch.setattr(seam, "_compat_context", lambda _client, _request: context)
    rate = RateAcquirer(store=PostgresRateStore(), max_wait=1.0, lease_ttl=30.0)
    grants = CatalogGrantResolver(
        _GrantSession(model.id, model.provider.id, 8100 + user_id),
    )
    foundation = DispatchFoundation(
        grants=grants,
        rate=rate,
        admission=AtomicLLMAdmissionService(),
        settlement=LLMSettlementService(),
        authority=_Authority(revoked=revoked),
    )
    monkeypatch.setattr(seam, "_compat_foundation", lambda: foundation)
    monkeypatch.setattr(settings, "LLM_RATE_RPM", 60)
    monkeypatch.setattr(settings, "LLM_RATE_TPM", 0)
    monkeypatch.setattr(settings, "LLM_RATE_CONCURRENCY", 0)
    calls = []

    class SyntheticTransport:
        async def post(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
                    "cost": "0.000125",
                },
                request=httpx.Request("POST", url),
            )

    @asynccontextmanager
    async def fake_provider_http_client():
        yield SyntheticTransport()

    monkeypatch.setattr(llm_client, "provider_http_client", fake_provider_http_client)
    with tenant_scope(tenant_id):
        if revoked:
            with pytest.raises(seam.DispatchDenial) as raised:
                await _client(model).chat([{"role": "user", "content": "synthetic prompt"}])
            assert raised.value.code == seam.CODE_PLAN_GRANT_MISSING
        else:
            response = await _client(model).chat([{"role": "user", "content": "synthetic prompt"}])
            assert response.dispatch_id == dispatch_id
    assert len(calls) == (0 if revoked else 1)
    async with async_session_maker() as session:
        state = await session.scalar(
            select(LLMDispatchReservationState).where(LLMDispatchReservationState.dispatch_id == dispatch_id)
        )
        assert state is not None
        day = await session.scalar(select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id))
        cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == user_id)
        )
        lease = await session.scalar(select(LLMDispatchLease).where(LLMDispatchLease.lease_id == dispatch_id))
        terminal_events = (
            (
                await session.execute(
                    select(LLMDispatchReservationEvent).where(
                        LLMDispatchReservationEvent.dispatch_id == dispatch_id,
                        LLMDispatchReservationEvent.event_kind.in_(("settled", "not_dispatched")),
                    )
                )
            )
            .scalars()
            .all()
        )
        outcome = await session.scalar(select(LLMOutcomeEvent).where(LLMOutcomeEvent.dispatch_id == dispatch_id))
        assert lease is not None and lease.released_at is not None
        assert len(terminal_events) == 1 and outcome is not None
        assert day.reserved_usd == 0
        if revoked:
            assert state.status == "not_dispatched"
            assert state.actual_tokens == 0 and state.actual_usd == 0
            assert cycle.reserved_tokens == 0 and cycle.consumed_tokens == 0
            assert day.settled_usd == 0
        else:
            assert state.status == "settled"
            assert state.actual_tokens == 12 and state.actual_usd == Decimal("0.000125")
            assert cycle.reserved_tokens == 0 and cycle.consumed_tokens == 12
            assert day.settled_usd == Decimal("0.000125")
