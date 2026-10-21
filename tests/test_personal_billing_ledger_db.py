"""Opt-in PostgreSQL acceptance for immutable personal-ledger settlement.

No schema setup, migration, reset or row cleanup occurs here. Synthetic rows
use the `pblg-`/`pblg+` markers recorded in `tests/acceptance/personal-billing.md`.
Run sequentially only after the owner authorizes the migration and DB test scope.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    LLMModel,
    LLMProvider,
    PersonalBillingAccount,
    PersonalBillingCutover,
    PersonalLLMBoundPolicy,
    PersonalLLMBudgetDay,
    PlatformRole,
    Role,
    Tenant,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.services.ai.outcome_events import build_outcome_event
from app.services.billing.personal_ledger import observe_personal_outcome, reserve_personal_dispatch


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
@pytest.mark.parametrize("actual_cost,expected_incident", [("0.100000000", "bound_breach"), ("2.000000000", "bound_and_cap_overrun")])
async def test_provider_actual_overshoot_is_settled_fully_and_freezes_account(
    monkeypatch, actual_cost: str, expected_incident: str
):
    """Persist over-reservation price as immutable charge, even across bounds."""
    from app.models import PersonalLLMBillingIncident, PersonalLLMDispatch, PersonalLLMReservationState

    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    marker = uuid4().hex
    tenant = await Tenant.objects.create(slug=f"pblg-{marker}", name=f"PBLG {marker}", plan="business")
    platform_admin = await PlatformRole.objects.get(codename="admin")
    workspace_owner = await WorkspaceRole.objects.get(codename="owner")
    user = await User.objects.create(
        username=f"pblg-{marker}",
        email=f"pblg-{marker}@example.invalid",
        hashed_password="synthetic-test-only",
        role_id=platform_admin.id,
        is_active=True,
    )
    await TenantUser.objects.create(
        tenant_id=tenant.id,
        user_id=user.id,
        role_id=workspace_owner.id,
        channel="web",
        external_user_id=f"pblg+{marker}",
        is_active=True,
    )
    account = await PersonalBillingAccount.objects.create(
        user_id=user.id,
        daily_limit_usd=Decimal("1"),
        is_active=True,
        configured_at=datetime.now(timezone.utc),
        policy_version="personal-daily-cap-v1",
    )
    now = datetime.now(timezone.utc)
    midnight = datetime.combine(now.date(), time.min, timezone.utc)
    await PersonalBillingCutover.objects.create(
        account_id=account.id,
        effective_at=midnight,
        reviewed_by_user_id=user.id,
        review_version="acceptance-clean-midnight-v1",
    )
    provider = await LLMProvider.objects.create(
        name=f"pblg-{marker}",
        api_format="openai",
        base_url="https://example.invalid/v1",
        is_active=True,
    )
    model = await LLMModel.objects.create(
        provider_id=provider.id,
        name=f"PBLG {marker}",
        model_id=f"pblg-{marker}",
        model_type="text",
        input_cost_per_1k=1.0,
        output_cost_per_1k=1.0,
        cache_read_cost_per_1m=0,
        cache_write_5m_cost_per_1m=0,
        cache_write_1h_cost_per_1m=0,
        max_tokens=64,
        default_temperature=0.0,
        is_active=True,
    )
    await PersonalLLMBoundPolicy.objects.create(
        provider_row_id=provider.id,
        model_row_id=model.id,
        provider_name=provider.name,
        model_id=model.model_id,
        version="acceptance-v1",
        max_input_tokens=63,
        max_output_tokens=1,
        request_mode="text_only",
        reviewed_by_user_id=user.id,
    )

    with tenant_scope(tenant.id), permission_scope(user):
        admission = await reserve_personal_dispatch(
            await LLMModel.objects.select_related("provider").get(id=model.id),
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape={
                "adapter": "openai_chat_completions",
                "provider_name": provider.name,
                "model_id": model.model_id,
                "has_media": False,
                "uses_tools": False,
                "requested_output_tokens": 1,
            },
        )
        receipt = {key: value for key, value in admission.items() if not key.startswith("__")}
        event = build_outcome_event(
            receipt,
            event_id=uuid4().hex,
            observed_at=datetime.now(timezone.utc),
            dispatch_state="response_received",
            outcome="succeeded",
            accounting_envelope={
                "accounting_usage": {"prompt_tokens": 8, "completion_tokens": 1},
                "accounting_cost_reported": True,
                "accounting_cost_usd": actual_cost,
            },
        )
        from app.models import LLMOutcomeEvent

        await LLMOutcomeEvent.objects.record_event(event)
        await observe_personal_outcome(tenant_id=tenant.id, event_id=event["event_id"])

    assert (await PersonalLLMDispatch.objects.get(dispatch_id=receipt["dispatch_id"])).payer_user_id == user.id
    state = await PersonalLLMReservationState.objects.get(dispatch_id=receipt["dispatch_id"])
    assert state.status == "settled"
    assert state.settled_usd == Decimal(actual_cost)
    day = await PersonalLLMBudgetDay.objects.get(account_id=account.id, utc_day=now.date())
    assert day.frozen is True
    incident = await PersonalLLMBillingIncident.objects.get(event_id=event["event_id"])
    assert incident.incident_kind == expected_incident


async def _acceptance_environment(monkeypatch, marker: str):
    """Create an owned acceptance account with a reviewed request bound."""
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    tenant = await Tenant.objects.create(slug=f"pblg-{marker}", name=f"PBLG {marker}", plan="business")
    platform_admin = await PlatformRole.objects.get(codename="admin")
    workspace_owner = await WorkspaceRole.objects.get(codename="owner")
    user = await User.objects.create(
        username=f"pblg-{marker}",
        email=f"pblg-{marker}@example.invalid",
        hashed_password="synthetic-test-only",
        role_id=platform_admin.id,
        is_active=True,
    )
    await TenantUser.objects.create(
        tenant_id=tenant.id,
        user_id=user.id,
        role_id=workspace_owner.id,
        channel="web",
        external_user_id=f"pblg+{marker}",
        is_active=True,
    )
    account = await PersonalBillingAccount.objects.create(
        user_id=user.id,
        daily_limit_usd=Decimal("1"),
        is_active=True,
        configured_at=datetime.now(timezone.utc),
        policy_version="personal-daily-cap-v1",
    )
    now = datetime.now(timezone.utc)
    midnight = datetime.combine(now.date(), time.min, timezone.utc)
    await PersonalBillingCutover.objects.create(
        account_id=account.id,
        effective_at=midnight,
        reviewed_by_user_id=user.id,
        review_version="acceptance-clean-midnight-v1",
    )
    provider = await LLMProvider.objects.create(
        name=f"pblg-{marker}",
        api_format="openai",
        base_url="https://example.invalid/v1",
        is_active=True,
    )
    model = await LLMModel.objects.create(
        provider_id=provider.id,
        name=f"PBLG {marker}",
        model_id=f"pblg-{marker}",
        model_type="text",
        input_cost_per_1k=1.0,
        output_cost_per_1k=1.0,
        cache_read_cost_per_1m=0,
        cache_write_5m_cost_per_1m=0,
        cache_write_1h_cost_per_1m=0,
        max_tokens=64,
        default_temperature=0.0,
        is_active=True,
    )
    await PersonalLLMBoundPolicy.objects.create(
        provider_row_id=provider.id,
        model_row_id=model.id,
        provider_name=provider.name,
        model_id=model.model_id,
        version="acceptance-v1",
        max_input_tokens=63,
        max_output_tokens=1,
        request_mode="text_only",
        reviewed_by_user_id=user.id,
    )
    loaded = await LLMModel.objects.select_related("provider").get(id=model.id)
    return {
        "tenant_id": tenant.id,
        "user_id": user.id,
        "user": user,
        "account_id": account.id,
        "now": now,
        "model": loaded,
        "provider_name": provider.name,
        "model_id": loaded.model_id,
    }


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
async def test_unknown_cost_terminal_conflict_freezes_day_and_replays_idempotently(monkeypatch):
    """A post-terminal unknown-cost event freezes the day with a NULL actual and replays without a duplicate."""
    from app.models import (
        LLMOutcomeEvent,
        PersonalLLMBillingIncident,
        PersonalLLMBudgetDay,
        PersonalLLMReservationState,
    )
    from app.models.managers.personal_llm_billing_manager import PersonalDispatchConflict

    marker = uuid4().hex
    env = await _acceptance_environment(monkeypatch, marker)
    request_shape = {
        "adapter": "openai_chat_completions",
        "provider_name": env["provider_name"],
        "model_id": env["model_id"],
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }
    with tenant_scope(env["tenant_id"]), permission_scope(env["user"]):
        admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=request_shape,
        )
        receipt = {key: value for key, value in admission.items() if not key.startswith("__")}
        settled_observed = max(datetime.now(timezone.utc), receipt["prepared_at"])
        settled = build_outcome_event(
            receipt,
            event_id=uuid4().hex,
            observed_at=settled_observed,
            dispatch_state="response_received",
            outcome="succeeded",
            accounting_envelope={
                "accounting_usage": {"prompt_tokens": 4, "completion_tokens": 1},
                "accounting_cost_reported": True,
                "accounting_cost_usd": "0.050000000",
            },
        )
        await LLMOutcomeEvent.objects.record_event(settled)
        await observe_personal_outcome(tenant_id=env["tenant_id"], event_id=settled["event_id"])

        conflict_observed = max(datetime.now(timezone.utc), settled_observed)
        conflict = build_outcome_event(
            receipt,
            event_id=uuid4().hex,
            observed_at=conflict_observed,
            dispatch_state="possible",
            outcome="cancelled",
        )
        await LLMOutcomeEvent.objects.record_event(conflict)
        with pytest.raises(PersonalDispatchConflict):
            await observe_personal_outcome(tenant_id=env["tenant_id"], event_id=conflict["event_id"])

        replayed = await observe_personal_outcome(tenant_id=env["tenant_id"], event_id=conflict["event_id"])
        assert replayed is False

    incident = await PersonalLLMBillingIncident.objects.get(event_id=conflict["event_id"])
    assert incident.incident_kind == "terminal_conflict"
    assert incident.actual_usd is None
    assert await PersonalLLMBillingIncident.objects.filter(event_id=conflict["event_id"]).count() == 1
    day = await PersonalLLMBudgetDay.objects.get(
        account_id=env["account_id"], utc_day=receipt["prepared_at"].date()
    )
    assert day.frozen is True


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
@pytest.mark.parametrize("initiator_role,should_admit", [("operator", True), ("user", False)])
async def test_task_dispatch_allows_only_trusted_nonmember_operator_initiator(
    monkeypatch, initiator_role: str, should_admit: bool
):
    """A task's fixed consenting payer is retained; only platform operators bypass initiator membership."""
    from app.models import AgentTask
    from app.models.managers.personal_billing_manager import PersonalBillingUnavailable

    env = await _acceptance_environment(monkeypatch, f"{uuid4().hex}-{initiator_role}")
    role = await PlatformRole.objects.get(codename=initiator_role)
    initiator = await User.objects.create(
        username=f"pblg-initiator-{uuid4().hex}",
        email=f"pblg-initiator-{uuid4().hex}@example.invalid",
        hashed_password="synthetic-test-only",
        role_id=role.id,
        is_active=True,
    )
    with tenant_scope(env["tenant_id"]), permission_scope(env["user"], is_owner=True):
        task = await AgentTask.objects.create(
            tenant_id=env["tenant_id"], name=f"PBLG task {uuid4().hex}", cron_expr="* * * * *", job_type="reflect"
        )
        consent = await PersonalBillingAccount.objects.consent_task_payer(task.id)
    assert consent.payer_user_id == env["user_id"]

    request_shape = {
        "adapter": "openai_chat_completions",
        "provider_name": env["provider_name"],
        "model_id": env["model_id"],
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }
    with tenant_scope(env["tenant_id"]), permission_scope(initiator):
        if not should_admit:
            with pytest.raises(PersonalBillingUnavailable, match="initiator"):
                await reserve_personal_dispatch(
                    env["model"],
                    purpose="analysis",
                    stage="text_analysis",
                    execution={"agent_task_id": task.id, "initiated_by_user_id": initiator.id},
                    request_shape=request_shape,
                )
            return
        admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"agent_task_id": task.id, "initiated_by_user_id": initiator.id},
            request_shape=request_shape,
        )

    from app.models import PersonalLLMDispatch

    dispatch = await PersonalLLMDispatch.objects.get(dispatch_id=admission["dispatch_id"])
    assert dispatch.payer_user_id == env["user_id"]
    assert dispatch.initiated_by_user_id == initiator.id


def _inject_undefined_tariff(monkeypatch):
    """Make the stored provider snapshot represent a genuinely missing ordinary rate."""
    from app.models.managers import personal_llm_billing_manager

    original_builder = personal_llm_billing_manager.build_dispatch_receipt

    def build_undefined_receipt(*args, **kwargs):
        receipt = original_builder(*args, **kwargs)
        receipt["output_cost_per_1k"] = None
        receipt["tariff_state"] = "undefined"
        return receipt

    monkeypatch.setattr(personal_llm_billing_manager, "build_dispatch_receipt", build_undefined_receipt)
    return original_builder


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
async def test_undefined_tariff_is_rejected_before_receipt_or_dispatch_for_finite_cap(monkeypatch):
    """A finite cap cannot reserve a dispatch whose price obligation is undefined."""
    from app.models import LLMDispatchReceipt, PersonalLLMDispatch
    from app.services.ai.budget_admission import BudgetAdmissionDenied

    env = await _acceptance_environment(monkeypatch, uuid4().hex)
    _inject_undefined_tariff(monkeypatch)
    request_shape = {
        "adapter": "openai_chat_completions",
        "provider_name": env["provider_name"],
        "model_id": env["model_id"],
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }

    with tenant_scope(env["tenant_id"]), permission_scope(env["user"]):
        with pytest.raises(BudgetAdmissionDenied, match="reviewed provider/model request bound"):
            await reserve_personal_dispatch(
                env["model"],
                purpose="analysis",
                stage="text_analysis",
                execution={"source_id": None},
                request_shape=request_shape,
            )

        assert await LLMDispatchReceipt.objects.filter(tenant_id=env["tenant_id"]).count() == 0
        assert await PersonalLLMDispatch.objects.filter(account_id=env["account_id"]).count() == 0


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
async def test_unlimited_unknown_hold_blocks_later_finite_cap_admission(monkeypatch):
    """Unlimited accounts may track unknown obligations, which block later capped calls while unresolved."""
    from app.models import (
        LLMDispatchReceipt,
        PersonalLLMDispatch,
        PersonalLLMReservation,
        PersonalLLMReservationState,
    )
    from app.services.ai.budget_admission import BudgetAdmissionDenied
    from app.models.managers import personal_llm_billing_manager

    env = await _acceptance_environment(monkeypatch, uuid4().hex)
    original_builder = _inject_undefined_tariff(monkeypatch)
    request_shape = {
        "adapter": "openai_chat_completions",
        "provider_name": env["provider_name"],
        "model_id": env["model_id"],
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }

    with tenant_scope(env["tenant_id"]), permission_scope(env["user"]):
        await PersonalBillingAccount.objects.configure_self(Decimal("0"))
        admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=request_shape,
        )
        receipt = {key: value for key, value in admission.items() if not key.startswith("__")}
        reservation = await PersonalLLMReservation.objects.get(dispatch_id=receipt["dispatch_id"])
        state = await PersonalLLMReservationState.objects.get(dispatch_id=receipt["dispatch_id"])
        assert reservation.ceiling_state == "undefined"
        assert state.status == "reserved"

        await PersonalBillingAccount.objects.configure_self(Decimal("1"))
        monkeypatch.setattr(personal_llm_billing_manager, "build_dispatch_receipt", original_builder)
        with pytest.raises(BudgetAdmissionDenied, match="Current-day unresolved unknown liability"):
            await reserve_personal_dispatch(
                env["model"],
                purpose="analysis",
                stage="text_analysis",
                execution={"source_id": None},
                request_shape=request_shape,
            )

        assert await LLMDispatchReceipt.objects.filter(tenant_id=env["tenant_id"]).count() == 1
        assert await PersonalLLMDispatch.objects.filter(account_id=env["account_id"]).count() == 1


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
async def test_self_spend_summary_separates_settled_sources_and_open_reservations(monkeypatch):
    """Report each canonical dispatch once and distinguish paid evidence from open liability."""
    from app.models import LLMOutcomeEvent, PersonalBillingAccount

    env = await _acceptance_environment(monkeypatch, uuid4().hex)
    request_shape = {
        "adapter": "openai_chat_completions",
        "provider_name": env["provider_name"],
        "model_id": env["model_id"],
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }

    with tenant_scope(env["tenant_id"]), permission_scope(env["user"]):
        reported_admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=request_shape,
        )
        reported_receipt = {key: value for key, value in reported_admission.items() if not key.startswith("__")}
        reported = build_outcome_event(
            reported_receipt,
            event_id=uuid4().hex,
            observed_at=max(datetime.now(timezone.utc), reported_receipt["prepared_at"]),
            dispatch_state="response_received",
            outcome="succeeded",
            accounting_envelope={
                "accounting_usage": {"prompt_tokens": 8, "completion_tokens": 1},
                "accounting_cost_reported": True,
                "accounting_cost_usd": "0.000005000",
            },
        )
        await LLMOutcomeEvent.objects.record_event(reported)
        await observe_personal_outcome(tenant_id=env["tenant_id"], event_id=reported["event_id"])

        reconstructed_admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=request_shape,
        )
        reconstructed_receipt = {
            key: value for key, value in reconstructed_admission.items() if not key.startswith("__")
        }
        reconstructed = build_outcome_event(
            reconstructed_receipt,
            event_id=uuid4().hex,
            observed_at=max(datetime.now(timezone.utc), reconstructed_receipt["prepared_at"]),
            dispatch_state="response_received",
            outcome="succeeded",
            accounting_envelope={
                "accounting_usage": {"prompt_tokens": 8, "completion_tokens": 1},
                "accounting_cache_usage": {
                    "format": "openai",
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cache_write_5m_tokens": None,
                    "cache_write_1h_tokens": None,
                },
            },
        )
        await LLMOutcomeEvent.objects.record_event(reconstructed)
        await observe_personal_outcome(tenant_id=env["tenant_id"], event_id=reconstructed["event_id"])

        await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=request_shape,
        )
        await PersonalBillingAccount.objects.configure_self(Decimal("0"))
        _inject_undefined_tariff(monkeypatch)
        await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=request_shape,
        )
        summary = await PersonalBillingAccount.objects.self_spend_summary()

    assert summary["dispatch_count"] == 4
    assert summary["provider_reported_usd"] == "0.000005000"
    assert summary["tariff_reconstructed_usd"] == "0.000009000"
    assert summary["settled_usd"] == "0.000014000"
    assert Decimal(summary["reserved_usd"]) > 0
    assert summary["pending_count"] == 2
    assert summary["unknown_count"] == 1
    assert summary["complete"] is False
    assert summary["legacy_history_included"] is False
    assert len(summary["by_workspace"]) == 1
    assert summary["by_workspace"][0]["tenant_id"] == env["tenant_id"]
