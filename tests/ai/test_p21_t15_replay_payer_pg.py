"""P21-T15 isolated-schema evidence: exact replay and payer-scoped admission.

These tests exercise real managers, real sessions and the real PostgreSQL
ledger. Only holder policy resolution, the daily USD cap lookup and network /
HTTP capability are doubled; no financial manager, session or ledger is mocked
and no test opens a raw DBAPI connection. Immutable fixtures are retained until
the coordinator drops the entire task-owned disposable schema after the run.
Every test requires ``P21_T15_ISOLATED_SCHEMA`` to name the task-owned schema
and refuses ``public`` / ``test_schema`` before any data is touched.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    AgentTask,
    LLMBudgetDay,
    LLMBudgetReservation,
    LLMCatalog,
    LLMDispatchLease,
    LLMDispatchReceipt,
    LLMDispatchReservationEvent,
    LLMDispatchReservationState,
    LLMModel,
    LLMProvider,
    LLMTokenAllowanceCycle,
    LLMTokenAllowanceReservation,
    PersonalBillingAccount,
    PersonalBillingCutover,
    PersonalLLMBoundPolicy,
    PersonalLLMBudgetDay,
    PersonalLLMDispatch,
    PersonalLLMLedgerEntry,
    PersonalLLMReservation,
    PersonalLLMReservationState,
    PlatformRole,
    TaskBillingPayer,
    Tenant,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.models.llm.llm_dispatch_receipt import LLMDispatchReceipt as ReceiptRow
from app.services.ai import dispatch_receipts as receipt_module
from app.services.ai.atomic_admission import AtomicLLMAdmissionService
from app.services.ai.budget_admission import BudgetAdmissionDenied
from app.services.ai.dispatch_seam import (
    FUNDING_CLASS_PERSONAL,
    FUNDING_CLASS_PLATFORM,
    DispatchContext,
    DispatchDenial,
    DispatchRequest,
)

POLICY_VERSION = 11
CREDENTIAL_VERSION = 1
TOKEN_CEILING = 64


def _require_isolated_schema() -> str:
    target_schema = os.environ.get("P21_T15_ISOLATED_SCHEMA")
    if not target_schema:
        pytest.skip("P21_T15_ISOLATED_SCHEMA must name the task-owned isolated schema")
    if target_schema in {"public", "test_schema"} or settings.DB_SCHEMA != target_schema:
        pytest.fail("P21-T15 replay/payer evidence requires its own non-public, non-shared schema")
    return target_schema


async def _assert_tables(session, *names: str) -> None:
    present = await session.execute(
        text("SELECT " + ", ".join(f"to_regclass(:t{i})" for i in range(len(names)))),
        {f"t{i}": f"{settings.DB_SCHEMA}.{name}" for i, name in enumerate(names)},
    )
    assert all(present.one()), "Apply P21-T15 and its prerequisites to the isolated schema first"


async def _role_id(session, model, codename: str) -> int:
    role_id = await session.scalar(select(model.id).where(model.codename == codename))
    assert role_id is not None, f"Seeded {codename!r} role is required in the isolated schema"
    return role_id


async def _new_user(session, role_id: int, marker: str, label: str) -> User:
    name = f"p21t15-{marker}-{label}"
    user = User(
        username=name,
        email=f"{name}@localhost.invalid",
        hashed_password="synthetic-hash-never-a-credential",
        role_id=role_id,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _new_tenant(session, holder: User, marker: str, label: str) -> Tenant:
    tenant = Tenant(
        name=f"P21-T15 {marker} {label}",
        slug=f"p21t15-{marker}-{label}",
        plan="pro",
        plan_holder_user_id=holder.id,
        is_active=True,
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def _member(session, tenant_id: int, user: User, marker: str) -> None:
    session.add(
        TenantUser(
            tenant_id=tenant_id,
            user_id=user.id,
            channel="web",
            external_user_id=f"p21t15-{marker}-member-{user.id}",
            is_active=True,
        )
    )
    await session.flush()


async def _lease(session, dispatch_id: str, now: datetime) -> None:
    session.add(
        LLMDispatchLease(
            lease_id=dispatch_id,
            scope="upstream",
            scope_key="p21t15-replay-payer-provider",
            model_family="p21t15-replay-payer-family",
            expires_at=now + timedelta(minutes=5),
        )
    )
    await session.flush()


def _model_double(model: LLMModel, provider: LLMProvider, *, context_window: int = TOKEN_CEILING):
    """The real rows, with the reviewed envelope fields the seam reads."""
    return SimpleNamespace(
        id=model.id,
        model_id=model.model_id,
        context_window_tokens=context_window,
        max_output_tokens=1,
        requested_output_tokens=1,
        capabilities=["text"],
        pricing_token_unit=model.pricing_token_unit,
        input_cost_per_1k=Decimal(str(model.input_cost_per_1k)),
        output_cost_per_1k=Decimal(str(model.output_cost_per_1k)),
        cache_read_cost_per_1m=model.cache_read_cost_per_1m,
        cache_write_5m_cost_per_1m=model.cache_write_5m_cost_per_1m,
        cache_write_1h_cost_per_1m=model.cache_write_1h_cost_per_1m,
        provider=SimpleNamespace(id=provider.id, name=provider.name, api_format=provider.api_format),
    )


def _install_policy(monkeypatch, holder_id: int, *, token_limit: int | None = 10_000):
    """Only holder policy resolution and the USD cap lookup are doubled."""
    from app.services.ai import atomic_admission as admission_module

    async def resolve_policy(_session, tenant_id, actor_user_id, **_):
        return SimpleNamespace(
            plan_holder_user_id=holder_id,
            plan_policy_version=POLICY_VERSION,
            actor_user_id=actor_user_id,
            policy=SimpleNamespace(
                token_policy=SimpleNamespace(unit="token", token_limit=token_limit, policy_version=POLICY_VERSION)
            ),
        )

    monkeypatch.setattr(admission_module, "resolve_holder_context", resolve_policy)
    monkeypatch.setattr("app.services.tenancy.resolver.resolved_daily_cost_limit", lambda _: 10.0)


async def _count(session, model, *conditions) -> int:
    stmt = select(func.count()).select_from(model)
    for condition in conditions:
        stmt = stmt.where(condition)
    return int(await session.scalar(stmt))


@pytest.mark.asyncio
async def test_persisted_exact_replay_denies_before_http_capable_reservation(monkeypatch):
    """A committed admission is replay evidence only, never a second provider call."""
    _require_isolated_schema()
    marker = uuid.uuid4().hex[:10]
    dispatch_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    holder_id = tenant_id = model_id = None
    async with async_session_maker() as session:
        await _assert_tables(
            session,
            "llm_dispatch_receipts",
            "llm_budget_reservations",
            "llm_budget_days",
            "llm_token_allowance_cycles",
            "llm_token_allowance_reservations",
            "llm_dispatch_reservation_states",
            "llm_dispatch_reservation_events",
        )
        role_id = await _role_id(session, PlatformRole, "user")
        holder = await _new_user(session, role_id, marker, "holder")
        tenant = await _new_tenant(session, holder, marker, "space")
        await _member(session, tenant.id, holder, marker)
        provider = LLMProvider(
            name=f"p21t15-replay-{marker}",
            uid=f"p21t15-replay-{marker}",
            base_url="https://replay.invalid/v1",
            api_format="openai",
            is_active=True,
            catalog_id=await _catalog(session, None),
        )
        session.add(provider)
        await session.flush()
        model = LLMModel(
            provider_id=provider.id,
            catalog_id=provider.catalog_id,
            cache_read_cost_per_1m=0,
            cache_write_5m_cost_per_1m=0,
            cache_write_1h_cost_per_1m=0,
            name=f"P21-T15 replay {marker}",
            model_id=f"p21t15-replay-{marker}",
            model_type="text",
            input_cost_per_1k=0.1,
            output_cost_per_1k=0.2,
            pricing_token_unit=1000,
            context_window_tokens=TOKEN_CEILING,
            max_output_tokens=1,
            max_tokens=64,
            default_temperature=0.0,
            is_active=True,
        )
        session.add(model)
        await session.flush()
        holder_id, tenant_id, model_id = holder.id, tenant.id, model.id
        session.add(
            LLMBudgetDay(
                tenant_id=tenant.id,
                utc_day=now.date(),
                settled_usd=Decimal(0),
                reserved_usd=Decimal(0),
                undefined_count=0,
                unbounded_count=0,
                frozen=False,
                baseline_version="p21t15-replay-payer-v1",
            )
        )
        await _lease(session, dispatch_id, now)
        await session.commit()

    _install_policy(monkeypatch, holder_id)
    # build_dispatch_receipt snapshots the real persisted model/provider rows;
    # only the reviewed envelope fields are supplied by the double.
    real_model = await _load_model(model_id)
    model_double = _model_double(real_model, real_model.provider)
    request = DispatchRequest(model=model_double, purpose="chat", stage="chat", dispatch_id=dispatch_id)
    context = DispatchContext(
        actor_user_id=holder_id,
        tenant_id=tenant_id,
        plan_holder_user_id=holder_id,
        plan_policy_version=POLICY_VERSION,
        catalog_id=str(real_model.catalog_id),
        funding_class=FUNDING_CLASS_PLATFORM,
        credential_ref="p21t15-test-managed",
        payer=f"tenant:{tenant_id}",
        credential_version=CREDENTIAL_VERSION,
    )
    with tenant_scope(tenant_id):
        first = await AtomicLLMAdmissionService().reserve(request, context)
    assert first.dispatch_id == dispatch_id
    assert first.credential_version == CREDENTIAL_VERSION

    async with async_session_maker() as session:
        assert await _count(session, LLMDispatchReceipt, LLMDispatchReceipt.dispatch_id == dispatch_id) == 1
        assert await _count(session, LLMBudgetReservation, LLMBudgetReservation.dispatch_id == dispatch_id) == 1
        assert (
            await _count(session, LLMTokenAllowanceReservation, LLMTokenAllowanceReservation.dispatch_id == dispatch_id)
            == 1
        )
        baseline_day = await session.scalar(
            select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id, LLMBudgetDay.utc_day == now.date())
        )
        baseline_cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == holder_id)
        )
        day_reserved = baseline_day.reserved_usd
        cycle_reserved, cycle_consumed = baseline_cycle.reserved_tokens, baseline_cycle.consumed_tokens
        stored = await session.scalar(select(ReceiptRow).where(ReceiptRow.dispatch_id == dispatch_id))
        exact_receipt = {field: getattr(stored, field) for field in receipt_module.RECEIPT_FIELDS}
        exact_receipt["initiated_by_user_id"] = None

    # Pin the pure receipt builder to the durable snapshot. A replay is the
    # SAME receipt identity; allowing a fresh prepared_at would turn this
    # into an ambiguous-conflict test instead of an exact-replay test.
    monkeypatch.setattr(
        "app.services.ai.dispatch_receipts.build_dispatch_receipt",
        lambda *_, **__: dict(exact_receipt),
    )
    with tenant_scope(tenant_id):
        with pytest.raises(BudgetAdmissionDenied, match="Exact receipt replay"):
            await AtomicLLMAdmissionService().reserve(request, context)
    # The actual manager recognized the durable snapshot as replay; the
    # service rejected it before making another token reservation.
    from app.services.ai import dispatch_seam as seam

    async def identity(request, hints):
        return hints

    async def fresh(admission):
        return admission.context

    async def grants(request, ctx):
        return seam.DispatchGrant(catalog_id=ctx.catalog_id)

    async def rate(request, ctx):
        return seam.DispatchLease(lease_id=request.dispatch_id)

    noop = AsyncMock()
    foundation = seam.DispatchFoundation(
        authority=SimpleNamespace(
            resolve_identity=identity, resolve_credential=identity, resolve_payer=identity, revalidate=fresh
        ),
        grants=SimpleNamespace(resolve=grants),
        rate=SimpleNamespace(acquire=rate, release=noop),
        admission=AtomicLLMAdmissionService(),
        settlement=SimpleNamespace(settle=noop),
    )
    ctx = replace(
        context,
        workspace_plan=SimpleNamespace(
            policy=SimpleNamespace(token_policy=SimpleNamespace(unit="token", token_limit=10_000))
        ),
    )
    http = AsyncMock()

    async def provider_attempt():
        admitted = await seam.admit(request, foundation=foundation, context=ctx)
        await http(admitted)

    with tenant_scope(tenant_id):
        with pytest.raises(DispatchDenial) as denied:
            await provider_attempt()
    assert denied.value.code == "admission_unavailable"
    http.assert_not_awaited()

    async with async_session_maker() as session:
        assert await _count(session, LLMDispatchReceipt, LLMDispatchReceipt.dispatch_id == dispatch_id) == 1
        assert await _count(session, LLMBudgetReservation, LLMBudgetReservation.dispatch_id == dispatch_id) == 1
        assert (
            await _count(session, LLMTokenAllowanceReservation, LLMTokenAllowanceReservation.dispatch_id == dispatch_id)
            == 1
        )
        assert (
            await _count(session, LLMDispatchReservationState, LLMDispatchReservationState.dispatch_id == dispatch_id)
            == 1
        )
        assert (
            await _count(session, LLMDispatchReservationEvent, LLMDispatchReservationEvent.dispatch_id == dispatch_id)
            == 1
        )
        day = await session.scalar(
            select(LLMBudgetDay).where(LLMBudgetDay.tenant_id == tenant_id, LLMBudgetDay.utc_day == now.date())
        )
        cycle = await session.scalar(
            select(LLMTokenAllowanceCycle).where(LLMTokenAllowanceCycle.plan_holder_user_id == holder_id)
        )
        assert day.reserved_usd == day_reserved
        assert day.settled_usd == Decimal(0)
        assert cycle.reserved_tokens == cycle_reserved == TOKEN_CEILING
        assert cycle.consumed_tokens == cycle_consumed == 0


@pytest.mark.asyncio
async def test_task_payer_billing_uses_consented_user_not_authenticated_actor(monkeypatch):
    """Real PersonalLLMBillingManager binds the personal dispatch to task payer B."""
    _require_isolated_schema()
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    marker = uuid.uuid4().hex[:10]
    dispatch_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    midnight = datetime.combine(now.date(), time.min, timezone.utc)
    ids: dict[str, int] = {}
    async with async_session_maker() as session:
        await _assert_tables(
            session,
            "personal_billing_accounts",
            "personal_billing_cutovers",
            "personal_llm_bound_policies",
            "personal_llm_budget_days",
            "personal_llm_dispatches",
            "personal_llm_reservations",
            "personal_llm_reservation_states",
            "personal_llm_ledger_entries",
            "task_billing_payers",
            "agent_tasks",
        )
        user_role = await _role_id(session, PlatformRole, "user")
        owner_role = await _role_id(session, WorkspaceRole, "owner")
        actor = await _new_user(session, user_role, marker, "actor-a")
        payer = await _new_user(session, user_role, marker, "payer-b")
        reviewer = await _new_user(session, await _role_id(session, PlatformRole, "admin"), marker, "reviewer")
        other = await _new_user(session, user_role, marker, "other-c")
        tenant = await _new_tenant(session, actor, marker, "space")
        await _member(session, tenant.id, actor, marker)
        await _member(session, tenant.id, payer, marker)
        task = AgentTask(
            name=f"p21t15-task-{marker}",
            tenant_id=tenant.id,
            cron_expr="0 9 * * *",
            job_type="analyze",
            is_active=True,
        )
        session.add(task)
        await session.flush()
        provider = LLMProvider(
            name=f"p21t15-personal-{marker}",
            uid=f"p21t15-personal-{marker}",
            base_url="https://personal.invalid/v1",
            api_format="openai",
            owner_user_id=payer.id,
            is_active=True,
            catalog_id=await _catalog(session, payer.id),
        )
        session.add(provider)
        await session.flush()
        model = LLMModel(
            provider_id=provider.id,
            catalog_id=provider.catalog_id,
            cache_read_cost_per_1m=0,
            cache_write_5m_cost_per_1m=0,
            cache_write_1h_cost_per_1m=0,
            name=f"P21-T15 personal {marker}",
            model_id=f"p21t15-personal-{marker}",
            model_type="text",
            input_cost_per_1k=0.1,
            output_cost_per_1k=0.2,
            pricing_token_unit=1000,
            max_tokens=64,
            default_temperature=0.0,
            owner_user_id=payer.id,
            is_active=True,
        )
        session.add(model)
        await session.flush()
        session.add(
            PersonalLLMBoundPolicy(
                provider_row_id=provider.id,
                model_row_id=model.id,
                provider_name=provider.name,
                model_id=model.model_id,
                version="p21t15-personal-v1",
                max_input_tokens=63,
                max_output_tokens=1,
                request_mode="text_only",
                reviewed_by_user_id=reviewer.id,
            )
        )
        for user in (payer, other):
            account = PersonalBillingAccount(
                user_id=user.id,
                daily_limit_usd=Decimal("5"),
                is_active=True,
                configured_at=now,
                policy_version="personal-daily-cap-v1",
            )
            session.add(account)
            await session.flush()
            session.add(
                PersonalBillingCutover(
                    account_id=account.id,
                    effective_at=midnight,
                    reviewed_by_user_id=reviewer.id,
                    review_version="clean-midnight-v1",
                )
            )
            session.add(
                PersonalLLMBudgetDay(
                    account_id=account.id,
                    utc_day=now.date(),
                    settled_usd=Decimal(0),
                    reserved_usd=Decimal(0),
                    undefined_count=0,
                    unbounded_count=0,
                    unresolved_count=0,
                    frozen=False,
                    baseline_version="p21t15-personal-v1",
                )
            )
            ids[f"account_{user.id}"] = account.id
        ids.update(
            actor=actor.id,
            payer=payer.id,
            other=other.id,
            tenant=tenant.id,
            task=task.id,
            model=model.id,
            provider=provider.id,
            owner_role=owner_role,
        )
        session.add(
            TaskBillingPayer(
                agent_task_id=task.id,
                tenant_id=tenant.id,
                payer_user_id=payer.id,
                consented_by_user_id=payer.id,
                consent_version=1,
            )
        )
        await session.commit()

    from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager

    actor = SimpleNamespace(id=ids["actor"])
    real_model = await _load_model(ids["model"])
    execution = {"agent_task_id": ids["task"], "initiated_by_user_id": ids["actor"]}
    request_shape = {
        "adapter": "openai_chat_completions",
        "provider_name": real_model.provider.name,
        "model_id": real_model.model_id,
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }
    with tenant_scope(ids["tenant"]), permission_scope(actor):
        admission = await PersonalLLMBillingManager().reserve_dispatch(
            real_model,
            purpose="analysis",
            stage="text_analysis",
            execution=execution,
            request_shape=request_shape,
            dispatch_id=dispatch_id,
            undefined_outside_cap=True,
        )
    assert admission["__credential_version"] == CREDENTIAL_VERSION

    async with async_session_maker() as session:
        dispatch = await session.scalar(
            select(PersonalLLMDispatch).where(PersonalLLMDispatch.dispatch_id == dispatch_id)
        )
        assert dispatch is not None
        assert dispatch.payer_user_id == ids["payer"]
        assert dispatch.initiated_by_user_id == ids["actor"]
        assert dispatch.tenant_id == ids["tenant"]
        assert dispatch.agent_task_id == ids["task"]
        assert dispatch.account_id == ids[f"account_{ids['payer']}"]

        reservation = await session.scalar(
            select(PersonalLLMReservation).where(PersonalLLMReservation.dispatch_id == dispatch_id)
        )
        state = await session.scalar(
            select(PersonalLLMReservationState).where(PersonalLLMReservationState.dispatch_id == dispatch_id)
        )
        ledger = (
            (
                await session.execute(
                    select(PersonalLLMLedgerEntry).where(PersonalLLMLedgerEntry.dispatch_id == dispatch_id)
                )
            )
            .scalars()
            .all()
        )
        assert reservation is not None and state is not None
        assert reservation.account_id == ids[f"account_{ids['payer']}"]
        assert state.status == "reserved"
        assert len(ledger) == 1
        assert ledger[0].entry_kind == "reserve"
        assert ledger[0].account_id == ids[f"account_{ids['payer']}"]

        payer_day = await session.scalar(
            select(PersonalLLMBudgetDay).where(PersonalLLMBudgetDay.account_id == ids[f"account_{ids['payer']}"])
        )
        other_day = await session.scalar(
            select(PersonalLLMBudgetDay).where(PersonalLLMBudgetDay.account_id == ids[f"account_{ids['other']}"])
        )
        assert payer_day.reserved_usd == state.reserved_usd
        assert payer_day.unresolved_count == 1
        assert other_day.reserved_usd == Decimal(0)
        assert other_day.unresolved_count == 0
        assert (
            await _count(session, PersonalLLMDispatch, PersonalLLMDispatch.account_id == ids[f"account_{ids['other']}"])
            == 0
        )
        assert (
            await _count(
                session, PersonalLLMLedgerEntry, PersonalLLMLedgerEntry.account_id == ids[f"account_{ids['other']}"]
            )
            == 0
        )

        tenant_usd = (
            await session.execute(
                select(func.count())
                .select_from(LLMBudgetReservation)
                .where(LLMBudgetReservation.tenant_id == ids["tenant"])
            )
        ).scalar_one()
        assert tenant_usd == 0
        assert await _count(session, LLMBudgetDay, LLMBudgetDay.tenant_id == ids["tenant"]) == 0


@pytest.mark.asyncio
async def test_wrong_context_personal_payer_denies_before_token_reservation(monkeypatch):
    """A payer/context mismatch rolls back the real personal reservation."""
    _require_isolated_schema()
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    marker = uuid.uuid4().hex[:10]
    dispatch_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    midnight = datetime.combine(now.date(), time.min, timezone.utc)
    ids: dict[str, int] = {}
    async with async_session_maker() as session:
        await _assert_tables(
            session,
            "personal_billing_accounts",
            "personal_billing_cutovers",
            "personal_llm_bound_policies",
            "personal_llm_budget_days",
            "personal_llm_dispatches",
            "personal_llm_reservations",
            "personal_llm_reservation_states",
            "personal_llm_ledger_entries",
            "task_billing_payers",
            "agent_tasks",
            "llm_token_allowance_cycles",
        )
        user_role = await _role_id(session, PlatformRole, "user")
        actor = await _new_user(session, user_role, marker, "actor")
        payer = await _new_user(session, user_role, marker, "payer")
        reviewer = await _new_user(session, await _role_id(session, PlatformRole, "admin"), marker, "reviewer")
        tenant = await _new_tenant(session, actor, marker, "space")
        await _member(session, tenant.id, actor, marker)
        await _member(session, tenant.id, payer, marker)
        task = AgentTask(
            name=f"p21t15-wrong-context-{marker}",
            tenant_id=tenant.id,
            cron_expr="0 9 * * *",
            job_type="analyze",
            is_active=True,
        )
        session.add(task)
        await session.flush()
        provider = LLMProvider(
            name=f"p21t15-wrong-ctx-{marker}",
            uid=f"p21t15-wrong-ctx-{marker}",
            base_url="https://wrong-context.invalid/v1",
            api_format="openai",
            owner_user_id=payer.id,
            is_active=True,
            catalog_id=await _catalog(session, payer.id),
        )
        session.add(provider)
        await session.flush()
        model = LLMModel(
            provider_id=provider.id,
            catalog_id=provider.catalog_id,
            cache_read_cost_per_1m=0,
            cache_write_5m_cost_per_1m=0,
            cache_write_1h_cost_per_1m=0,
            name=f"P21-T15 wrong context {marker}",
            model_id=f"p21t15-wrong-ctx-{marker}",
            model_type="text",
            input_cost_per_1k=0.1,
            output_cost_per_1k=0.2,
            pricing_token_unit=1000,
            context_window_tokens=TOKEN_CEILING,
            max_output_tokens=1,
            max_tokens=64,
            default_temperature=0.0,
            owner_user_id=payer.id,
            is_active=True,
        )
        session.add(model)
        await session.flush()
        session.add(
            PersonalLLMBoundPolicy(
                provider_row_id=provider.id,
                model_row_id=model.id,
                provider_name=provider.name,
                model_id=model.model_id,
                version="p21t15-personal-v1",
                max_input_tokens=63,
                max_output_tokens=1,
                request_mode="text_only",
                reviewed_by_user_id=reviewer.id,
            )
        )
        account = PersonalBillingAccount(
            user_id=payer.id,
            daily_limit_usd=Decimal("5"),
            is_active=True,
            configured_at=now,
            policy_version="personal-daily-cap-v1",
        )
        session.add(account)
        await session.flush()
        session.add(
            PersonalBillingCutover(
                account_id=account.id,
                effective_at=midnight,
                reviewed_by_user_id=reviewer.id,
                review_version="clean-midnight-v1",
            )
        )
        session.add(
            PersonalLLMBudgetDay(
                account_id=account.id,
                utc_day=now.date(),
                settled_usd=Decimal(0),
                reserved_usd=Decimal(0),
                undefined_count=0,
                unbounded_count=0,
                unresolved_count=0,
                frozen=False,
                baseline_version="p21t15-personal-v1",
            )
        )
        await _lease(session, dispatch_id, now)
        ids.update(
            actor=actor.id,
            payer=payer.id,
            tenant=tenant.id,
            task=task.id,
            model=model.id,
            provider=provider.id,
            account=account.id,
        )
        session.add(
            TaskBillingPayer(
                agent_task_id=task.id,
                tenant_id=tenant.id,
                payer_user_id=payer.id,
                consented_by_user_id=payer.id,
                consent_version=1,
            )
        )
        await session.commit()

    _install_policy(monkeypatch, actor.id)
    real_model = await _load_model(ids["model"])
    model_double = _model_double(real_model, real_model.provider)
    execution = {"agent_task_id": ids["task"], "initiated_by_user_id": ids["actor"]}
    request = DispatchRequest(
        model=real_model,
        purpose="analysis",
        stage="text_analysis",
        execution=execution,
        dispatch_id=dispatch_id,
    )
    # The task consent says personal:B, but the trusted context claims A.
    context = DispatchContext(
        actor_user_id=ids["actor"],
        tenant_id=ids["tenant"],
        plan_holder_user_id=ids["actor"],
        plan_policy_version=POLICY_VERSION,
        catalog_id=str(real_model.catalog_id),
        funding_class=FUNDING_CLASS_PERSONAL,
        payer=f"personal:{ids['actor']}",
        credential_version=CREDENTIAL_VERSION,
    )
    with tenant_scope(ids["tenant"]), permission_scope(SimpleNamespace(id=ids["actor"])):
        with pytest.raises(BudgetAdmissionDenied, match="Personal payer changed"):
            await AtomicLLMAdmissionService().reserve(request, context)

    async with async_session_maker() as session:
        # The real personal manager wrote then rolled back its receipt,
        # dispatch, reservation, state and append-only ledger entry.
        for model_class in (
            PersonalLLMDispatch,
            PersonalLLMReservation,
            PersonalLLMReservationState,
            PersonalLLMLedgerEntry,
            LLMDispatchReceipt,
            LLMTokenAllowanceReservation,
            LLMDispatchReservationState,
            LLMDispatchReservationEvent,
        ):
            assert await _count(session, model_class, model_class.dispatch_id == dispatch_id) == 0
        assert (
            await _count(session, LLMTokenAllowanceCycle, LLMTokenAllowanceCycle.plan_holder_user_id == ids["actor"])
            == 0
        )
        day = await session.scalar(
            select(PersonalLLMBudgetDay).where(PersonalLLMBudgetDay.account_id == ids["account"])
        )
        assert day.reserved_usd == Decimal(0)
        assert day.settled_usd == Decimal(0)
        assert day.unresolved_count == 0
        assert day.undefined_count == 0
        assert day.unbounded_count == 0


async def _catalog(session, owner_id):
    row = await session.scalar(select(LLMCatalog).where(LLMCatalog.owner_user_id == owner_id))
    if row is None:
        row = LLMCatalog(kind="platform" if owner_id is None else "personal", owner_user_id=owner_id, is_active=True)
        session.add(row)
        await session.flush()
    return row.id


async def _load_model(identity):
    async with async_session_maker() as session:
        return await session.scalar(
            select(LLMModel).where(LLMModel.id == identity).options(selectinload(LLMModel.provider))
        )
