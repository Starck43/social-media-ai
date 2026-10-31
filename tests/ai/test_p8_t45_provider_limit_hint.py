"""Provider limit hints use only safe structured evidence; no provider calls."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
probe = load_isolated_source("_p8_t45_model_probe", ROOT / "app/services/ai/model_probe.py")


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"tier": "real", "http_status": 429}, "rate_limit"),
        ({"tier": "real", "http_status": 402}, "billing"),
        ({"tier": "real", "response_json": {"error": {"code": "insufficient_quota"}}}, "quota"),
        ({"tier": "real", "response_json": {"error": {"message": "monthly limit exceeded"}}}, "budget"),
        ({"tier": "real", "error_code": "requests per minute limit"}, "rate_limit"),
        ({"tier": "real", "response_json": {"error": "insufficient balance"}}, "billing"),
    ],
)
def test_recognizes_structured_quota_budget_and_rate_evidence(result, expected):
    hint = probe.provider_limit_hint(result)
    assert hint["code"] == expected
    assert hint["message"]
    assert result.get("limit_hint") is None  # classification is pure


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"quota": {"remaining": 0}}, "quota"),
        ({"limits": {"daily": {"limit": 10, "used": 10}}}, "budget"),
        ({"error": {"message": "quota exhausted"}}, "quota"),
    ],
)
def test_success_json_limit_metadata_is_detected(payload, expected):
    assert probe.provider_limit_hint({"tier": "real", "http_status": 200, "response_json": payload})["code"] == expected


def test_generated_choice_text_does_not_trigger_quota_warning():
    payload = {"choices": [{"message": {"content": "quota exhausted"}}]}
    assert probe.provider_limit_hint({"tier": "real", "http_status": 200, "response_json": payload}) is None


def test_structured_quota_evidence_beats_generic_rate_limit_status():
    result = {"tier": "real", "http_status": 429, "response_json": {"quota": {"remaining": 0}}}
    assert probe.provider_limit_hint(result)["code"] == "quota"


def test_reports_price_mismatch_for_free_model_without_changing_tariff():
    model = NS(input_cost_per_1k=0, output_cost_per_1k=0)
    hint = probe.provider_limit_hint({"tier": "real", "provider_cost_usd": "0.02"}, model)
    assert hint["code"] == "pricing_mismatch"
    assert model.input_cost_per_1k == 0 and model.output_cost_per_1k == 0


@pytest.mark.parametrize("cost", [None, 0, "0", "NaN", "Infinity", "invalid", -1])
def test_missing_or_invalid_price_does_not_create_warning(cost):
    assert (
        probe.provider_limit_hint(
            {"tier": "real", "provider_cost_usd": cost}, NS(input_cost_per_1k=0, output_cost_per_1k=0)
        )
        is None
    )


def test_paid_catalog_model_and_non_real_probe_do_not_get_free_price_warning():
    model = NS(input_cost_per_1k=0.1, output_cost_per_1k=0.2)
    assert probe.provider_limit_hint({"tier": "real", "provider_cost_usd": 1}, model) is None
    assert probe.provider_limit_hint({"tier": "mock", "http_status": 429}, model) is None


def test_arbitrary_provider_error_text_never_appears_in_hint():
    secret = "fixture-secret-123"
    hint = probe.provider_limit_hint(
        {
            "tier": "real",
            "response_json": {"error": {"message": "quota exceeded " + secret}},
        }
    )
    assert hint["code"] == "quota"
    assert secret not in repr(hint)


@pytest.mark.asyncio
async def test_real_model_test_forwards_accounting_purpose_once_to_client_and_journal():
    from unittest.mock import AsyncMock, Mock

    owner_id = 2
    raw = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}
    client = NS(api_key="fixture-key", timeout=1.0)

    async def chat(messages, **kwargs):
        client._probe_diagnostics.update(
            stage="response", dispatch_state="confirmed", raw_response=raw, http_status=200
        )
        return {"content": "ok", "_personal_dispatch_id": "fixture-dispatch"}

    client.chat = AsyncMock(side_effect=chat)
    journal = AsyncMock()
    llm = NS(LLMClientFactory=NS(create=Mock(return_value=client)), journal_chat_attempt=journal)
    attempt_journal = NS(provider_cost_evidence=lambda response: (False, None))
    module = load_isolated_source(
        "app.services.ai._p8_t45_real_probe",
        ROOT / "app/services/ai/model_probe.py",
        {
            "app.services.ai.llm_client": llm,
            "app.services.ai.attempt_journal": attempt_journal,
        },
    )
    model = NS(
        id=7,
        owner_user_id=owner_id,
        name="Fixture",
        model_id="fixture-model",
        max_tokens=2048,
        input_cost_per_1k=0,
        output_cost_per_1k=0,
        last_error_at=None,
        last_success_at=None,
        is_active=True,
        provider=NS(
            owner_user_id=owner_id,
            api_format="openai",
            is_active=True,
            get_api_key=lambda: "fixture-key",
        ),
    )
    result = await module.probe_real_capped(model, purpose="model_test")
    assert result["ok"] is True
    client.chat.assert_awaited_once()
    assert client.chat.await_args.kwargs["personal_billing_purpose"] == "model_test"
    assert client.chat.await_args.kwargs["max_tokens"] == 8
    journal.assert_awaited_once_with(client, "model_test", {"content": "ok"}, dispatch_id="fixture-dispatch")


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"quota": {"remaining": "0"}}, "quota"),
        ({"quota": {"remaining": "NaN"}}, None),
        ({"quota": {"remaining": "Infinity"}}, None),
        ({"quota": {"remaining": True}}, None),
        ({"quota": {"remaining": "unknown"}}, None),
    ],
)
def test_limit_numbers_accept_finite_numeric_strings_and_ignore_invalid_values(payload, expected):
    hint = probe.provider_limit_hint({"tier": "real", "http_status": 200, "response_json": payload})
    assert (hint["code"] if hint else None) == expected


def test_no_json_error_code_still_produces_safe_quota_hint():
    hint = probe.provider_limit_hint(
        {
            "tier": "real",
            "http_status": 500,
            "response_json": None,
            "response_body": "not-json",
            "error_code": "insufficient_quota",
        }
    )
    assert hint and hint["code"] == "quota"
    assert "not-json" not in repr(hint)
