from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from starlette.datastructures import FormData
from wtforms import Form

from app.admin.price_fields import ExactModelPriceField
from app.api.v1.endpoints.llm_models import _raw_price_updates
from app.models.billing.dispatch import PersonalLLMDispatch
from app.models.llm.llm_attempt import LLMAttempt
from app.models.llm.llm_dispatch_receipt import LLMDispatchReceipt
from app.models.llm.llm_model import LLMModel
from app.models.llm.llm_outcome_event import LLMOutcomeEvent
from app.schemas.llm_model import LLMModelCreate, LLMModelUpdate
from app.services.ai.dispatch_receipts import RECEIPT_FIELDS, build_dispatch_receipt


class ModelPriceForm(Form):
    input_cost_per_1k = ExactModelPriceField()
    cache_read_cost_per_1m = ExactModelPriceField()
    cache_write_5m_cost_per_1m = ExactModelPriceField()


def test_admin_price_field_round_trips_raw_numbers_without_unit_conversion():
    initial = ModelPriceForm(
        data={
            "input_cost_per_1k": Decimal("0.12"),
            "cache_read_cost_per_1m": Decimal("0.024"),
            "cache_write_5m_cost_per_1m": None,
        }
    )
    assert initial.input_cost_per_1k._value() == "0.12"
    assert initial.cache_read_cost_per_1m._value() == "0.024"
    assert initial.cache_write_5m_cost_per_1m._value() == ""

    submitted = ModelPriceForm(
        formdata=FormData(
            {
                "input_cost_per_1k": "0.12",
                "cache_read_cost_per_1m": "0.024",
                "cache_write_5m_cost_per_1m": "0",
            }
        )
    )
    assert submitted.input_cost_per_1k.data == Decimal("0.12")
    assert submitted.cache_read_cost_per_1m.data == Decimal("0.024")
    assert submitted.cache_write_5m_cost_per_1m.data == Decimal(0)


def test_admin_blank_distinguishes_unconfigured_cache_from_existing_base_rates():
    submitted = ModelPriceForm(
        formdata=FormData(
            {
                "input_cost_per_1k": "",
                "cache_read_cost_per_1m": "",
                "cache_write_5m_cost_per_1m": "",
            }
        )
    )
    assert submitted.input_cost_per_1k.data == Decimal(0)
    assert submitted.cache_read_cost_per_1m.data is None
    assert submitted.cache_write_5m_cost_per_1m.data is None


def test_cache_tariff_schema_is_nullable_exact_decimal_evidence():
    rate_names = (
        "cache_read_cost_per_1m",
        "cache_write_5m_cost_per_1m",
        "cache_write_1h_cost_per_1m",
    )
    for model in (LLMModel, LLMDispatchReceipt, LLMAttempt):
        for name in rate_names:
            column = model.__table__.c[name]
            assert column.nullable
            assert column.type.precision == 18
            assert column.type.scale == 8
    assert LLMAttempt.__table__.c.cache_usage.nullable
    assert LLMAttempt.__table__.c.pricing_policy_version.nullable
    assert LLMOutcomeEvent.__table__.c.cache_usage.nullable
    assert LLMModel.__table__.c.pricing_token_unit.server_default.arg == "1000000"
    assert LLMDispatchReceipt.__table__.c.pricing_token_unit.nullable
    assert LLMAttempt.__table__.c.pricing_token_unit.nullable
    assert PersonalLLMDispatch.__table__.c.pricing_token_unit.nullable
    for name in rate_names:
        assert PersonalLLMDispatch.__table__.c[name].nullable


def test_personal_dispatch_accepts_the_complete_immutable_receipt_snapshot():
    provider = SimpleNamespace(id=2, name="Provider")
    model = SimpleNamespace(
        id=3,
        provider=provider,
        model_id="example",
        input_cost_per_1k=0.12,
        output_cost_per_1k=0.4,
        pricing_token_unit=1000000,
        cache_read_cost_per_1m=Decimal("0.024"),
        cache_write_5m_cost_per_1m=Decimal(0),
        cache_write_1h_cost_per_1m=Decimal(0),
    )
    snapshot = build_dispatch_receipt(
        model,
        tenant_id=1,
        purpose="analysis",
        stage="main",
        dispatch_id="a" * 32,
        prepared_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    personal = PersonalLLMDispatch(
        **{field: snapshot[field] for field in RECEIPT_FIELDS},
        account_id=4,
        payer_user_id=5,
        initiated_by_user_id=5,
    )
    assert personal.pricing_token_unit == 1000000
    assert personal.cache_read_cost_per_1m == Decimal("0.024")
    assert personal.cache_write_5m_cost_per_1m == Decimal(0)


def test_snapshot_guards_reject_ambiguous_units_and_incomplete_free_tariffs():
    attempt_guard = next(
        constraint
        for constraint in LLMAttempt.__table__.constraints
        if constraint.name == "ck_llm_attempts_pricing_token_unit"
    )
    receipt_guard = next(
        constraint
        for constraint in LLMDispatchReceipt.__table__.constraints
        if constraint.name == "ck_llm_dispatch_receipts_tariff_snapshot"
    )
    personal_guard = next(
        constraint
        for constraint in PersonalLLMDispatch.__table__.constraints
        if constraint.name == "ck_personal_llm_dispatch_snapshot"
    )
    assert "provider-price-v1" in str(attempt_guard.sqltext)
    assert "COALESCE" in str(attempt_guard.sqltext)
    assert "pricing_token_unit IS NULL AND input_cost_per_1k IS NULL" in str(attempt_guard.sqltext)
    for constraint in (receipt_guard, personal_guard):
        sql = str(constraint.sqltext)
        assert "COALESCE" in sql
        assert "pricing_token_unit IN (1000, 1000000)" in sql
        assert "pricing_token_unit IS NULL AND input_cost_per_1k IS NULL" not in sql
        assert "pricing_token_unit IS NULL OR pricing_token_unit = 1000" in sql
        assert "cache_write_1h_cost_per_1m IS NOT NULL" in sql


def test_model_api_bridges_canonical_per_million_and_legacy_per_1k_rates():
    common = {
        "name": "Example",
        "model_id": "example",
        "provider_id": 1,
        "model_type": "text",
        "pricing_token_unit": 1000000,
        "input_cost_per_1m": Decimal("0.12"),
        "input_cost_per_1k": Decimal("0.00012"),
        "cache_read_cost_per_1m": Decimal("0.024"),
        "cache_write_5m_cost_per_1m": Decimal(0),
        "cache_write_1h_cost_per_1m": None,
    }
    created = LLMModelCreate(**common)
    updated = LLMModelUpdate(**{key: common[key] for key in rate_names()})
    assert created.input_cost_per_1m == Decimal("0.12")
    assert created.input_cost_per_1k == Decimal("0.00012")
    assert created.cache_read_cost_per_1m == Decimal("0.024")
    assert created.cache_write_5m_cost_per_1m == Decimal(0)
    assert created.cache_write_1h_cost_per_1m is None
    assert updated.cache_read_cost_per_1m == Decimal("0.024")


def test_api_boundary_converts_between_true_units_without_mutating_raw_catalog_rates():
    legacy = _raw_price_updates({"input_cost_per_1k": Decimal("0.00012")}, 1000000)
    canonical = _raw_price_updates({"input_cost_per_1m": Decimal("0.12")}, 1000000)
    older_unit = _raw_price_updates({"input_cost_per_1m": Decimal("0.12")}, 1000)
    assert legacy["input_cost_per_1k"] == Decimal("0.12")
    assert canonical["input_cost_per_1k"] == Decimal("0.12")
    assert older_unit["input_cost_per_1k"] == Decimal("0.00012")


def test_per_million_projection_respects_selected_ordinary_rate_unit():
    model = SimpleNamespace(input_cost_per_1k=0.12, output_cost_per_1k=0.4, pricing_token_unit=1000000)
    assert LLMModel.get_cost_per_million(model) == (0.12, 0.4)


def rate_names() -> tuple[str, ...]:
    return ("cache_read_cost_per_1m", "cache_write_5m_cost_per_1m", "cache_write_1h_cost_per_1m")
