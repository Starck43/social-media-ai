"""Pure reservation-bound and tariff-freshness checks for cache pricing."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager


def shape():
    return {
        "adapter": "openai_chat_completions",
        "provider_name": "openai",
        "model_id": "reviewed-model",
        "has_media": False,
        "uses_tools": False,
        "requested_output_tokens": 1,
    }


def policy():
    return SimpleNamespace(
        max_input_tokens=1,
        max_output_tokens=1,
        request_mode="text_only",
        version="reviewed-v1",
    )


def v2_receipt(unit=1_000_000, *, read=".024", write_5m=".15", write_1h=".24"):
    return {
        "provider_name": "openai",
        "model_id": "reviewed-model",
        "pricing_policy_version": "cache-tariff-v2",
        "pricing_token_unit": unit,
        "tariff_state": "known",
        "input_cost_per_1k": Decimal(".12"),
        "output_cost_per_1k": Decimal(".4"),
        "cache_read_cost_per_1m": Decimal(read) if read is not None else None,
        "cache_write_5m_cost_per_1m": Decimal(write_5m) if write_5m is not None else None,
        "cache_write_1h_cost_per_1m": Decimal(write_1h) if write_1h is not None else None,
    }


def test_bound_uses_v2_per_million_unit_and_largest_cache_rate():
    state, ceiling, version = PersonalLLMBillingManager._request_bound(
        policy(), shape(), v2_receipt(read=".8", write_5m=".2", write_1h=".3")
    )
    assert (state, ceiling, version) == ("bounded", Decimal("0.000001600"), "reviewed-v1")


def test_bound_uses_v2_per_thousand_unit_for_legacy_catalog_rows():
    state, ceiling, _ = PersonalLLMBillingManager._request_bound(policy(), shape(), v2_receipt(1000))
    assert state == "bounded"
    assert ceiling == Decimal("0.000800000")


def test_missing_potentially_billable_cache_tariff_is_unbounded():
    receipt = v2_receipt(write_1h=None)
    state, ceiling, version = PersonalLLMBillingManager._request_bound(policy(), shape(), receipt)
    assert (state, ceiling, version) == ("unbounded", None, None)


def test_legacy_bound_preserves_historical_per_thousand_math():
    receipt = {
        "provider_name": "openai",
        "model_id": "reviewed-model",
        "tariff_state": "known",
        "input_cost_per_1k": Decimal(".12"),
        "output_cost_per_1k": Decimal(".4"),
    }
    state, ceiling, _ = PersonalLLMBillingManager._request_bound(policy(), shape(), receipt)
    assert (state, ceiling) == ("bounded", Decimal("0.000800000"))


def model_snapshot(**changes):
    provider = SimpleNamespace(id=7, name="openai", api_format="openai")
    values = {
        "model_id": "reviewed-model",
        "max_tokens": 512,
        "input_cost_per_1k": Decimal(".12"),
        "output_cost_per_1k": Decimal(".4"),
        "pricing_token_unit": 1_000_000,
        "cache_read_cost_per_1m": Decimal(".024"),
        "cache_write_5m_cost_per_1m": Decimal(".15"),
        "cache_write_1h_cost_per_1m": Decimal(".24"),
        "provider": provider,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pricing_token_unit", 1000),
        ("cache_read_cost_per_1m", Decimal(".025")),
        ("cache_write_5m_cost_per_1m", Decimal(".16")),
        ("cache_write_1h_cost_per_1m", Decimal(".25")),
    ],
)
def test_admission_freshness_compares_unit_and_every_cache_rate(field, value):
    selected = model_snapshot()
    fresh = model_snapshot(**{field: value})
    assert not PersonalLLMBillingManager._selected_model_matches_snapshot(fresh, selected, selected.provider)


def test_admission_freshness_accepts_unchanged_pricing_snapshot():
    selected = model_snapshot()
    fresh = model_snapshot()
    assert PersonalLLMBillingManager._selected_model_matches_snapshot(fresh, selected, selected.provider)
