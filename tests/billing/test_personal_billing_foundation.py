from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import PersonalBillingAccount, PersonalLLMDispatch, TaskBillingPayer, TenantScopedMixin
from app.models.billing import PersonalLLMReservation, PersonalLLMReservationState
from app.models.managers.personal_billing_manager import (
    PersonalBillingError,
    PersonalBillingManager,
    validated_daily_limit,
)
from app.models.scheduling.job import Job
from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager
from app.services.ai.dispatch_receipts import RECEIPT_FIELDS, build_dispatch_receipt
from app.services.ai.outcome_events import build_outcome_event
from app.services.ai.budget_admission import BudgetAdmissionDenied
from app.services.billing.errors import is_personal_billing_block


def test_daily_limit_requires_explicit_non_negative_decimal_policy():
    assert validated_daily_limit("0") == Decimal("0")
    assert validated_daily_limit("12.340000001") == Decimal("12.340000001")
    assert validated_daily_limit("1.0000000000") == Decimal("1.0000000000")
    with pytest.raises(ValueError):
        validated_daily_limit("-0.01")
    with pytest.raises(ValueError):
        validated_daily_limit("0.0000000001")
    with pytest.raises(ValueError):
        validated_daily_limit(True)
    with pytest.raises(ValueError):
        validated_daily_limit(1.25)


def test_personal_account_is_user_owned_not_workspace_scoped():
    assert not issubclass(PersonalBillingAccount, TenantScopedMixin)
    assert PersonalBillingAccount.__table__.c.daily_limit_usd.nullable
    assert ("user_id",) in {
        tuple(constraint.columns.keys())
        for constraint in PersonalBillingAccount.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }


def test_task_payer_binding_is_versioned_immutable_consent_evidence():
    constraints = TaskBillingPayer.__table__.constraints
    assert any(
        constraint.__class__.__name__ == "UniqueConstraint"
        and tuple(constraint.columns.keys()) == ("agent_task_id", "consent_version")
        for constraint in constraints
    )
    assert TaskBillingPayer.__table__.c.agent_task_id.foreign_keys == set()
    assert TaskBillingPayer.__table__.c.consented_by_user_id.foreign_keys


def test_personal_dispatch_keeps_its_own_tariff_and_attribution_snapshot():
    columns = PersonalLLMDispatch.__table__.c
    assert columns.dispatch_id.unique
    assert {"payer_user_id", "tenant_id", "agent_task_id", "tariff_state", "input_cost_per_1k"} <= set(columns.keys())
    assert columns.agent_task_id.foreign_keys == set()


def test_account_manager_requires_authenticated_non_bypass_actor(monkeypatch):
    monkeypatch.setattr("app.models.managers.personal_billing_manager.get_current_user", lambda: None)
    monkeypatch.setattr("app.models.managers.personal_billing_manager.is_bypass", lambda: False)
    with pytest.raises(PersonalBillingError):
        PersonalBillingManager._actor_id()


def test_reservations_states_and_dispatch_share_durable_identity_and_day():
    reservation = PersonalLLMReservation.__table__
    state = PersonalLLMReservationState.__table__
    assert any(
        constraint.__class__.__name__ == "UniqueConstraint" and tuple(constraint.columns.keys()) == ("dispatch_id",)
        for constraint in reservation.constraints
    )
    assert any(tuple(fk.column_keys) == ("dispatch_id", "account_id", "utc_day") for fk in state.foreign_key_constraints)
    assert "initiated_by_user_id" in Job.__table__.c
    assert Job.__table__.c.initiated_by_user_id.nullable


def test_receipt_and_outcome_keep_provider_cost_authority_and_task_initiator():
    provider = SimpleNamespace(id=4, name="openai")
    model = SimpleNamespace(
        id=5,
        provider=provider,
        model_id="reviewed-model",
        input_cost_per_1k=Decimal("2"),
        output_cost_per_1k=Decimal("5"),
    )
    prepared = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    record = build_dispatch_receipt(
        model,
        tenant_id=7,
        purpose="analysis",
        stage="text_analysis",
        dispatch_id="a" * 32,
        prepared_at=prepared,
        execution={"initiated_by_user_id": 11},
    )
    assert set(record) == set(RECEIPT_FIELDS) | {"initiated_by_user_id"}
    event = build_outcome_event(
        record,
        event_id="b" * 32,
        observed_at=prepared,
        dispatch_state="response_received",
        outcome="failed_output",
        accounting_envelope={
            "accounting_usage": {"prompt_tokens": 10, "completion_tokens": 3},
            "accounting_cost_reported": True,
            "accounting_cost_usd": "0.123456789",
        },
    )
    assert event["cost_usd"] == Decimal("0.123456789")
    assert event["price_source"] == "provider"


def test_billing_denials_are_classified_for_retry_propagation():
    assert is_personal_billing_block(BudgetAdmissionDenied("cap"))


def test_reviewed_bound_rounds_reservation_up_to_usd_quantum():
    policy = SimpleNamespace(
        max_input_tokens=1,
        max_output_tokens=1,
        request_mode="text_only",
        version="reviewed-v1",
    )
    receipt = {
        "provider_name": "openai",
        "model_id": "reviewed-model",
        "tariff_state": "known",
        "input_cost_per_1k": Decimal("0.00000001"),
        "output_cost_per_1k": Decimal("0.00000001"),
    }
    shape = {
        "adapter": "openai_chat_completions",
        "provider_name": "openai",
        "model_id": "reviewed-model",
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }
    state, ceiling, version = PersonalLLMBillingManager._request_bound(policy, shape, receipt)
    assert state == "bounded"
    assert ceiling == Decimal("0.000000001")
    assert version == "reviewed-v1"
