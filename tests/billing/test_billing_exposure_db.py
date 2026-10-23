"""Opt-in PostgreSQL acceptance for the operator financial exposure reads.

No schema setup, migration, reset or destructive cleanup occurs here. Synthetic
rows carry `pbex-`/`pbex+` markers. Receipts and outcome events are immutable
evidence (mutation triggers), so tenants/accounts bound to them persist with
their markers, exactly like the personal-ledger acceptance suite; the removable
catalog rows (providers/models) are deleted in a finally block.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    LLMModel,
    LLMProvider,
    PersonalBillingAccount,
    PersonalBillingCutover,
    PersonalLLMBoundPolicy,
    PlatformRole,
    Tenant,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.services.ai.dispatch_receipts import build_dispatch_receipt
from app.services.ai.outcome_events import build_outcome_event
from app.services.billing.personal_ledger import observe_personal_outcome, reserve_personal_dispatch

pytestmark = pytest.mark.tenancy


@pytest.fixture(autouse=True)
def _trusted_fixture_permission_scope():
    """Allow trusted DB fixture setup without enabling BaseManager bypass."""
    with operator_permission_scope():
        yield


async def _billing_environment(monkeypatch, marker: str) -> dict:
    """Create an owned billing account with a reviewed request bound."""
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    tenant = await Tenant.objects.create(slug=f"pbex-{marker}", name=f"PBEX {marker}", plan="business")
    platform_admin = await PlatformRole.objects.get(codename="admin")
    workspace_owner = await WorkspaceRole.objects.get(codename="owner")
    user = await User.objects.create(
        username=f"pbex-{marker}",
        email=f"pbex-{marker}@example.invalid",
        hashed_password="synthetic-test-only",
        role_id=platform_admin.id,
        is_active=True,
    )
    await TenantUser.objects.create(
        tenant_id=tenant.id,
        user_id=user.id,
        role_id=workspace_owner.id,
        channel="web",
        external_user_id=f"pbex+{marker}",
        is_active=True,
    )
    account = await PersonalBillingAccount.objects.create(
        user_id=user.id,
        daily_limit_usd=Decimal("1"),
        is_active=True,
        configured_at=datetime.now(timezone.utc),
        policy_version="personal-daily-cap-v1",
    )
    midnight = datetime.combine(datetime.now(timezone.utc).date(), time.min, timezone.utc)
    await PersonalBillingCutover.objects.create(
        account_id=account.id,
        effective_at=midnight,
        reviewed_by_user_id=user.id,
        review_version="acceptance-clean-midnight-v1",
    )
    with catalog_execution_scope(user.id):
        provider = await LLMProvider.objects.create(
            name=f"pbex-{marker}",
            api_format="openai",
            base_url="https://example.invalid/v1",
            is_active=True,
        )
        model = await LLMModel.objects.create(
            provider_id=provider.id,
            name=f"PBEX {marker}",
            model_id=f"pbex-{marker}",
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
        assert provider.owner_user_id == user.id
        assert model.owner_user_id == user.id
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
    with catalog_execution_scope(user.id):
        loaded = await LLMModel.objects.select_related("provider").get(id=model.id)
    return {
        "tenant_id": tenant.id,
        "user": user,
        "account_id": account.id,
        "model": loaded,
        "provider_name": provider.name,
        "model_id": loaded.model_id,
        "marker": marker,
    }


async def _catalog_user(marker: str) -> User:
    """Create an active actor who owns the fixture catalog rows."""
    role = await PlatformRole.objects.get(codename="admin")
    return await User.objects.create(
        username=f"pbex-catalog-{marker}",
        email=f"pbex-catalog-{marker}@example.invalid",
        hashed_password="synthetic-test-only",
        role_id=role.id,
        is_active=True,
    )


def _request_shape(env: dict) -> dict:
    return {
        "adapter": "openai_chat_completions",
        "provider_name": env["provider_name"],
        "model_id": env["model_id"],
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }


async def _settle(env: dict, amount: str) -> str:
    """Reserve one dispatch and settle it at an explicit provider-reported cost."""
    from app.models import LLMOutcomeEvent

    with catalog_execution_scope(env["user"].id), tenant_scope(env["tenant_id"]), permission_scope(env["user"]):
        admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=_request_shape(env),
        )
        receipt = {key: value for key, value in admission.items() if not key.startswith("__")}
        event = build_outcome_event(
            receipt,
            event_id=uuid4().hex,
            observed_at=max(datetime.now(timezone.utc), receipt["prepared_at"]),
            dispatch_state="response_received",
            outcome="succeeded",
            accounting_envelope={
                "accounting_usage": {"prompt_tokens": 8, "completion_tokens": 1},
                "accounting_cost_reported": True,
                "accounting_cost_usd": amount,
            },
        )
        await LLMOutcomeEvent.objects.record_event(event)
        await observe_personal_outcome(tenant_id=env["tenant_id"], event_id=event["event_id"])
    return receipt["dispatch_id"]


async def _reserve_open(env: dict) -> str:
    """Reserve one dispatch and leave it pending."""
    with catalog_execution_scope(env["user"].id), tenant_scope(env["tenant_id"]), permission_scope(env["user"]):
        admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=_request_shape(env),
        )
    return admission["dispatch_id"]


async def _reserve_unknown(monkeypatch, env: dict) -> str:
    """Reserve under an unlimited cap with an undefined-tariff receipt."""
    from app.models.managers import personal_llm_billing_manager

    original_builder = personal_llm_billing_manager.build_dispatch_receipt

    def build_undefined_receipt(*args, **kwargs):
        receipt = original_builder(*args, **kwargs)
        receipt["output_cost_per_1k"] = None
        receipt["tariff_state"] = "undefined"
        return receipt

    with catalog_execution_scope(env["user"].id), tenant_scope(env["tenant_id"]), permission_scope(env["user"]):
        await PersonalBillingAccount.objects.configure_self(Decimal("0"))
        monkeypatch.setattr(personal_llm_billing_manager, "build_dispatch_receipt", build_undefined_receipt)
        admission = await reserve_personal_dispatch(
            env["model"],
            purpose="analysis",
            stage="text_analysis",
            execution={"source_id": None},
            request_shape=_request_shape(env),
        )
    return admission["dispatch_id"]


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
async def test_workspace_summary_is_tenant_and_day_scoped_without_payer_identities(monkeypatch):
    """Aggregate one workspace's settled/reserved ledger for one day, aggregates only."""
    marker_a, marker_b = uuid4().hex, uuid4().hex
    env_a = await _billing_environment(monkeypatch, marker_a)
    env_b = await _billing_environment(monkeypatch, marker_b)
    day = datetime.now(timezone.utc).date()

    await _settle(env_a, "0.000005000")
    await _settle(env_a, "0.000004000")
    await _reserve_open(env_a)
    await _settle(env_b, "0.000003000")
    # Last write: switches the account to unlimited and the tariff to undefined.
    await _reserve_unknown(monkeypatch, env_a)

    with tenant_scope(env_a["tenant_id"]):
        summary = await PersonalBillingAccount.objects.workspace_spend_summary(env_a["tenant_id"], utc_day=day)

    assert summary["scope"] == "workspace_payer_bound_dispatches"
    assert summary["utc_day"] == day.isoformat()
    assert summary["currency"] == "USD"
    assert summary["dispatch_count"] == 4
    assert summary["settled_usd"] == "0.000009000"
    assert Decimal(summary["reserved_usd"]) == Decimal("0.000064")
    assert summary["provider_reported_usd"] == "0.000009000"
    assert summary["tariff_reconstructed_usd"] == "0"
    assert summary["pending_count"] == 2
    assert summary["unknown_count"] == 1
    assert summary["complete"] is False
    assert summary["legacy_history_included"] is False
    assert set(summary) == {
        "scope",
        "utc_day",
        "currency",
        "dispatch_count",
        "settled_usd",
        "reserved_usd",
        "provider_reported_usd",
        "tariff_reconstructed_usd",
        "pending_count",
        "unknown_count",
        "legacy_history_included",
        "complete",
    }
    # Aggregate only: no payer identity (user, workspace or account) leaks into the projection.
    assert marker_a not in json.dumps(summary)
    assert marker_b not in json.dumps(summary)

    with tenant_scope(env_a["tenant_id"]):
        other_day = await PersonalBillingAccount.objects.workspace_spend_summary(
            env_a["tenant_id"], utc_day=day + timedelta(days=1)
        )
    assert other_day["utc_day"] == (day + timedelta(days=1)).isoformat()
    assert other_day["dispatch_count"] == 0
    assert other_day["settled_usd"] == "0"

    with tenant_scope(env_b["tenant_id"]):
        summary_b = await PersonalBillingAccount.objects.workspace_spend_summary(env_b["tenant_id"], utc_day=day)
    assert summary_b["dispatch_count"] == 1
    assert summary_b["settled_usd"] == "0.000003000"
    assert summary_b["pending_count"] == 0
    assert summary_b["unknown_count"] == 0
    assert summary_b["complete"] is True


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
async def test_unpriced_models_counts_undefined_dispatch_attempts_per_tenant_and_window():
    """Only undefined-cost dispatched attempts count, once per dispatch, in scope."""
    from app.models import LLMDispatchReceipt, LLMOutcomeEvent

    marker = uuid4().hex
    tenant_a = await Tenant.objects.create(slug=f"pbex-{marker}a", name=f"PBEX A {marker}", plan="business")
    tenant_b = await Tenant.objects.create(slug=f"pbex-{marker}b", name=f"PBEX B {marker}", plan="business")
    catalog_user = await _catalog_user(marker)
    with catalog_execution_scope(catalog_user.id):
        provider = await LLMProvider.objects.create(
            name=f"pbex-{marker}",
            api_format="openai",
            base_url="https://example.invalid/v1",
            is_active=True,
        )
        model = await LLMModel.objects.create(
            provider_id=provider.id,
            name=f"PBEX {marker}",
            model_id=f"pbex-{marker}",
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
        assert provider.owner_user_id == catalog_user.id
        assert model.owner_user_id == catalog_user.id
        loaded = await LLMModel.objects.select_related("provider").get(id=model.id)
    now = datetime.now(timezone.utc)

    async def receipt_for(tenant_id: int, prepared_at: datetime) -> dict:
        with catalog_execution_scope(catalog_user.id):
            record = build_dispatch_receipt(
                loaded,
                tenant_id=tenant_id,
                purpose="analysis",
                stage="text_analysis",
                dispatch_id=uuid4().hex,
                prepared_at=prepared_at,
            )
        with tenant_scope(tenant_id):
            assert await LLMDispatchReceipt.objects.record_receipt(record) is True
        return record

    async def observe(
        record: dict,
        observed_at: datetime,
        *,
        dispatch_state: str = "possible",
        outcome: str = "timeout",
        envelope: dict | None = None,
    ) -> None:
        event = build_outcome_event(
            record,
            event_id=uuid4().hex,
            observed_at=observed_at,
            dispatch_state=dispatch_state,
            outcome=outcome,
            accounting_envelope=envelope,
        )
        with tenant_scope(record["tenant_id"]):
            assert await LLMOutcomeEvent.objects.record_event(event) is True

    try:
        undefined = await receipt_for(tenant_a.id, now)
        # Two observations of the same dispatch must count as one attempt.
        await observe(undefined, now + timedelta(seconds=1))
        await observe(undefined, now + timedelta(seconds=2), outcome="cancelled")
        priced = await receipt_for(tenant_a.id, now)
        await observe(
            priced,
            now + timedelta(seconds=1),
            dispatch_state="response_received",
            outcome="succeeded",
            envelope={
                "accounting_usage": {"prompt_tokens": 8, "completion_tokens": 1},
                "accounting_cost_reported": True,
                "accounting_cost_usd": "0.000001000",
            },
        )
        not_dispatched = await receipt_for(tenant_a.id, now)
        await observe(
            not_dispatched, now + timedelta(seconds=1), dispatch_state="not_dispatched", outcome="not_dispatched"
        )
        stale = await receipt_for(tenant_a.id, now - timedelta(hours=8))
        await observe(stale, now - timedelta(hours=8) + timedelta(seconds=1))
        foreign = await receipt_for(tenant_b.id, now)
        await observe(foreign, now + timedelta(seconds=1))

        with tenant_scope(tenant_a.id):
            recent = await LLMOutcomeEvent.objects.unpriced_models(
                tenant_a.id, since=now - timedelta(hours=1), limit=10
            )
            wide = await LLMOutcomeEvent.objects.unpriced_models(tenant_a.id, since=now - timedelta(hours=24), limit=10)

        with tenant_scope(tenant_b.id):
            other_tenant = await LLMOutcomeEvent.objects.unpriced_models(
                tenant_b.id, since=now - timedelta(hours=1), limit=10
            )

        assert len(recent) == 1
        assert set(recent[0]) == {"provider", "model_id", "attempts", "last_observed_at"}
        assert recent[0]["provider"] == provider.name
        assert recent[0]["model_id"] == model.model_id
        assert recent[0]["attempts"] == 1  # one dispatch despite two observations
        assert recent[0]["last_observed_at"] == now + timedelta(seconds=2)

        assert len(wide) == 1
        assert wide[0]["model_id"] == model.model_id
        assert wide[0]["attempts"] == 2  # the stale dispatch joins the wide window
        assert wide[0]["last_observed_at"] == now + timedelta(seconds=2)

        assert len(other_tenant) == 1
        assert other_tenant[0]["attempts"] == 1
        assert other_tenant[0]["model_id"] == model.model_id
    finally:
        # Receipts/events are immutable and keep their plain-integer references;
        # only the removable catalog rows are deleted.
        with catalog_execution_scope(catalog_user.id):
            await LLMProvider.objects.delete(id=provider.id)
        await User.objects.delete_user(catalog_user.id)


@pytest.mark.asyncio
@pytest.mark.personal_billing_db
async def test_zero_price_active_models_lists_only_active_zero_tariff_rows():
    """Only active 0/0 models are listed; total counts past the page limit."""

    marker = uuid4().hex
    catalog_user = await _catalog_user(marker)
    with catalog_execution_scope(catalog_user.id):
        before = await LLMModel.objects.zero_price_active_models(limit=50)
        provider = await LLMProvider.objects.create(
            name=f"pbex-{marker}",
            api_format="openai",
            base_url="https://example.invalid/v1",
            is_active=True,
        )
    try:
        with catalog_execution_scope(catalog_user.id):
            zero_models = [
                await LLMModel.objects.create(
                    provider_id=provider.id,
                    name=f"PBEX Zero {marker} {index}",
                    model_id=f"pbex-zero-{marker}-{index}",
                    model_type="text",
                    is_active=True,
                    is_default=index == 0,
                )
                for index in range(3)
            ]
            assert all(model.owner_user_id == catalog_user.id for model in zero_models)
            inactive = await LLMModel.objects.create(
                provider_id=provider.id,
                name=f"PBEX Inactive {marker}",
                model_id=f"pbex-inactive-{marker}",
                model_type="text",
                is_active=False,
            )
            nonzero = await LLMModel.objects.create(
                provider_id=provider.id,
                name=f"PBEX Paid {marker}",
                model_id=f"pbex-paid-{marker}",
                model_type="text",
                is_active=True,
                input_cost_per_1k=1.0,
                output_cost_per_1k=0.0,
            )

        with catalog_execution_scope(catalog_user.id):
            full = await LLMModel.objects.zero_price_active_models(limit=50)
        assert full["total"] == before["total"] + 3
        # All matching rows fit one page in this schema; a catalog that outgrew
        # the limit needs a tighter fixture, not weaker assertions.
        assert len(full["models"]) == full["total"]
        rows = {row["model_id"]: row for row in full["models"]}
        for model in zero_models:
            row = rows[model.model_id]
            assert set(row) == {"provider", "model_id", "name", "is_default"}
            assert row["provider"] == provider.name
            assert row["name"] == model.name
            assert row["is_default"] is bool(model.is_default)
        assert inactive.model_id not in rows
        assert nonzero.model_id not in rows

        with catalog_execution_scope(catalog_user.id):
            limited = await LLMModel.objects.zero_price_active_models(limit=2)
        assert limited["total"] == full["total"]
        assert len(limited["models"]) == 2
        assert all(row["provider"] for row in limited["models"])
    finally:
        with catalog_execution_scope(catalog_user.id):
            await LLMProvider.objects.delete(id=provider.id)
        await User.objects.delete_user(catalog_user.id)
