"""Pure journal pricing + actual probe/client/action over explicit no-DB doubles."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_admin_model_diagnostics import stack
from test_admin_model_probe import COST_EVIDENCE, ROOT, action_module, model, template_env

# Synthetic equivalent of the administrator's OpenRouter/Nvidia response.
REPLY = {
    "id": "gen-fixture-safety",
    "object": "chat.completion",
    "model": "nvidia/nemotron-3.5-content-safety:free",
    "provider": "Nvidia",
    "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": None}}],
    "usage": {
        "prompt_tokens": 470,
        "completion_tokens": 8,
        "total_tokens": 478,
        "cost": 0,
        "is_byok": False,
        "cost_details": {
            "upstream_inference_cost": 0,
            "upstream_inference_prompt_cost": 0,
            "upstream_inference_completions_cost": 0,
        },
        "completion_tokens_details": {"reasoning_tokens": 6, "image_tokens": 0, "audio_tokens": 0},
    },
}


@pytest.mark.asyncio
async def test_sample_owner_mode_keeps_price_zero_tokens_and_saves_tariff_estimate_even_without_text():
    probe, dependency, selected, sent, _ = stack(payload=deepcopy(REPLY))
    dependency.__isolated_imports__["app.core.config"].settings.PERSONAL_BILLING_ENABLED = False
    dependency._personal_admission = dependency._actual_personal_admission
    actions, manager, view = action_module(probe)
    manager.get.return_value = selected
    result = (await actions.LLMModelActions.submit_test(view, NS(), "7", "real", ""))["result"]
    assert len(sent) == 1 and result["dispatch_state"] == "response_received" and result["http_status"] == 200
    assert not result["ok"] and result["error_code"] == "empty_content" and result["finish_reason"] == "length"
    assert result["tokens_spent"] == 478 and result["usage"]["prompt_tokens"] == 470
    assert result["usage"]["completion_tokens"] == 8  # Includes reasoning; never add 6 again.
    assert result["provider_cost_usd"] == "0" and result["provider_cost_state"] == "resolved"
    assert result["estimated_cost"] == Decimal("0.000016047")  # Not provider-reported spend.
    manager.update_by_id.assert_awaited_once()
    assert manager.update_by_id.await_args.args == (7,)
    assert manager.update_by_id.await_args.kwargs["last_request_cost"] == pytest.approx(0.000016047)
    assert result["response_json"] == REPLY and result["accounting_status"] == "standard"
    dependency._record_llm_usage.assert_awaited_once()
    dependency.journal_chat_attempt.assert_awaited_once()
    # Legacy helper also recognizes this sample; the probe does not use it for pricing.
    info = dependency.extract_cost_info(REPLY)
    assert info["tokens"] == {"input": 470, "output": 8, "total": 478}
    assert info["has_cost_report"] and info["cost"]["total"] == 0


@pytest.mark.asyncio
async def test_scoped_journal_prices_sample_free_and_passes_exact_tokens_to_manager_double():
    envelope = COST_EVIDENCE.chat_attempt_envelope(
        model_id=REPLY["model"],
        provider_name="Nvidia",
        dispatch_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        raw=deepcopy(REPLY),
    )
    journal = NS(record_attempt=AsyncMock(return_value=True))
    resolver = AsyncMock(return_value={REPLY["model"]: {"id": 7, "input_cost_per_1k": 1, "output_cost_per_1k": 2}})
    assert (
        await COST_EVIDENCE.record_scoped_attempts(
            {COST_EVIDENCE.STAGE_CHAT: envelope},
            purpose="admin_probe",
            tenant_id=3,
            journal=journal,
            tariff_resolver=resolver,
        )
        == 1
    )
    row = journal.record_attempt.await_args.args[0]
    assert row["prompt_tokens"] == 470 and row["completion_tokens"] == 8
    assert row["cost_usd"] == Decimal(0) and row["cost_certainty"] == COST_EVIDENCE.CERTAINTY_FREE
    assert row["tenant_id"] == 3  # Storage is a double, not PostgreSQL persistence evidence.
    with pytest.raises(COST_EVIDENCE.MissingAttemptPayer):
        await COST_EVIDENCE.record_scoped_attempts(
            {COST_EVIDENCE.STAGE_CHAT: envelope},
            purpose="admin_probe",
            tenant_id=None,
            journal=journal,
        )
    journal.record_attempt.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage,currency,expected,state",
    [
        ({"cost": 0}, "USD", "0", "resolved"),
        ({"cost": "0.00000123"}, "USD", "0.00000123", "resolved"),
        ({"total_cost": 0, "cost": 9}, "USD", "0", "resolved"),
        ({"prompt_cost": 0, "completion_cost": 0}, "USD", "0", "resolved"),
        ({"cost": 2}, "EUR", None, "unresolved"),
        ({"cost": "99999999999"}, "USD", None, "unresolved"),
        ({}, "USD", None, "absent"),
        ({"cost": None}, "USD", None, "absent"),
        ({"cost": True}, "USD", None, "absent"),
        ({"cost": -1}, "USD", None, "absent"),
        ({"cost": "NaN"}, "USD", None, "absent"),
        ({"cost_details": {"upstream_inference_cost": 0}}, "USD", None, "absent"),
    ],
)
async def test_display_reuses_canonical_usd_evidence_without_assuming_free(usage, currency, expected, state):
    payload = {**deepcopy(REPLY), "usage": usage, "currency": currency}
    original = deepcopy(payload)
    probe, _, selected, sent, _ = stack(payload=payload)
    result = await probe.probe_real_capped(selected)
    assert result["provider_cost_state"] == state and result["provider_cost_usd"] == expected
    assert len(sent) == 1 and payload == original


@pytest.mark.parametrize(
    "http,finish,code,ok,expected",
    [
        (200, "stop", None, True, ("success", "success", "success")),
        (200, "length", "empty_content", False, ("success", "warning", "warning")),
        (429, None, "provider_http", False, ("danger", "neutral", "danger")),
        (None, None, "timeout", False, ("neutral", "neutral", "warning")),
        (200, "content_filter", "empty_content", False, ("success", "danger", "warning")),
    ],
)
def test_template_separates_http_finish_and_result_severity(http, finish, code, ok, expected):
    import re

    result = dict(
        tier="real",
        ok=ok,
        http_status=http,
        finish_reason=finish,
        text="fixture",
        provider_cost_state="resolved",
        provider_cost_usd="0",
        dispatch_state="response_received",
        response_json=REPLY,
    )
    if code:
        result["error_code"] = code
    template = template_env().from_string(
        (ROOT / "app/templates/llm_model/test_results.html")
        .read_text()
        .replace('{% extends "sqladmin/layout.html" %}', "")
    )
    html = template.render(model=model(), provider=model().provider, result=result)
    for name, tone in zip(["HTTP-статус API", "finish_reason", "Код результата"], expected):
        assert re.search(rf"<dt>{name}</dt><dd><span class=\"llm-state llm-state-{tone}\">", html)
    assert 'aria-label="Копировать JSON"' in html and '<svg class="llm-copy-icon"' in html
    assert 'class="llm-sr-only" id="llm-copy-status"' in html
