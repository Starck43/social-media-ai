"""Actual client and templates; explicit storage/admission/HTTP doubles, no DB."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from tests.shared.decomposition_source_loader import load_decomposed_source as load_isolated_source
from test_admin_model_billing_integration import endpoint_case
from test_admin_model_probe import COST_EVIDENCE, ROOT, action_module, model, probe_module, template_env

from app.core.personal_catalog_context import catalog_execution_scope

PAYLOAD = {
    "id": "chat-fixture",
    "model": "fixture-model",
    "created": 42,
    "choices": [{"message": {"role": "assistant", "content": "Привет!\n\nОтвет модели."}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    "provider_extra": {"nested": [1, True, "field"]},
}


def stack(*, payload=None, status=200, failure=None, accounting_failure=False, admission_failure=None, body=None):
    sent = []
    payload = PAYLOAD if payload is None else payload

    def transport(request):
        sent.append(request)
        if failure:
            raise failure
        return httpx.Response(status, json=payload) if body is None else httpx.Response(status, text=body)

    settings = NS(PERSONAL_BILLING_ENABLED=True, LLM_DEFAULT_TIMEOUT=60)
    http = NS(
        AsyncClient=lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        HTTPStatusError=httpx.HTTPStatusError,
        TimeoutException=httpx.TimeoutException,
        TransportError=httpx.TransportError,
    )
    client_module = load_isolated_source(
        "_diagnostic_client",
        ROOT / "app/services/ai/llm_client.py",
        {
            "app.core.config": NS(settings=settings),
            "app.models": NS(LLMModel=object),
            "app.services.ai.json_schema_builder": NS(validate_with_pydantic=Mock()),
            "app.services.ai.attempt_journal": COST_EVIDENCE,
            "app.services.billing.errors": NS(is_personal_billing_block=lambda error: False),
        },
        module_imports={"httpx": http},
    )
    client_module._actual_personal_admission = client_module._personal_admission
    client_module._personal_admission = AsyncMock(
        return_value=({"dispatch_id": "owned"}, "owned"), side_effect=admission_failure
    )
    client_module._verify_personal_dispatch_day = AsyncMock()
    # P3 credential revalidation owns the pre-HTTP DB check; the cost lane doubles it.
    client_module._revalidate_personal_dispatch_before_http = AsyncMock()
    client_module._observe_personal_outcome = AsyncMock(
        side_effect=RuntimeError("PRIVATE_ACCOUNTING") if accounting_failure else None
    )
    client_module._record_llm_usage = AsyncMock()
    client_module.journal_chat_attempt = AsyncMock()
    selected = model()
    selected.provider.base_url, selected.provider.auth_header = "https://provider.invalid/v1", None
    # The actual client asserts the bound personal-catalogue owner at construction.
    with catalog_execution_scope(selected.owner_user_id):
        client = client_module.OpenAICompatibleClient(selected)
    client._rate_limit = AsyncMock()
    client_module.LLMClientFactory.create = lambda selected: client
    probe = load_isolated_source(
        "_diagnostic_probe",
        ROOT / "app/services/ai/model_probe.py",
        {
            "app.core.config": NS(settings=settings),
            "app.services.ai.llm_client": client_module,
            "app.services.ai.attempt_journal": COST_EVIDENCE,
        },
    )
    return probe, client_module, selected, sent, client


@pytest.mark.asyncio
async def test_real_json_and_statuses_survive_actual_client_and_action():
    probe, _, selected, sent, _ = stack()
    actions, manager, view = action_module(probe)
    manager.get.return_value = selected
    result = (await actions.LLMModelActions.submit_test(view, NS(), "7", "real", "Вопрос"))["result"]
    assert result["ok"] and result["response_json"] == PAYLOAD
    assert result["http_status"] == 200 and result["dispatch_state"] == "response_received"
    assert result["stage"] == "complete" and result["accounting_status"] == "confirmed"
    assert result["finish_reason"] == "stop" and result["tokens_spent"] == 5
    assert result["content"] == PAYLOAD["choices"][0]["message"]["content"]
    assert "Привет!" in result["response_json_text"] and len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_provider_http_error_preserves_status_json_and_never_replays(status):
    probe, _, selected, sent, _ = stack(
        status=status, payload={"error": {"code": "provider_denied", "message": "API error"}}
    )
    result = await probe.probe_real_capped(selected)
    assert not result["ok"] and result["error_code"] == "provider_http"
    assert result["http_status"] == status and result["response_json"]["error"]["code"] == "provider_denied"
    assert result["dispatch_state"] == "response_received" and len(sent) == 1


@pytest.mark.asyncio
async def test_timeout_is_ambiguous_not_zero_or_retry():
    probe, _, selected, sent, _ = stack(failure=httpx.ReadTimeout("PRIVATE_TIMEOUT"))
    result = await probe.probe_real_capped(selected)
    assert result["error_code"] == "timeout" and result["tokens_spent"] is None
    assert result["dispatch_state"] == "possible" and result["http_status"] is None
    assert result["response_json"] is None and "PRIVATE_TIMEOUT" not in str(result) and len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,code",
    [
        ("Payer has no configured personal daily limit", "personal_account_required"),
        ("Reviewed clean-day billing boundary is not active", "billing_cutover_required"),
        ("Capped payer requires a reviewed provider/model request bound", "model_bound_required"),
        ("Personal daily limit would be exceeded", "daily_limit_exceeded"),
        ("PRIVATE_UNRECOGNIZED_SQL", "admission_denied"),
    ],
)
async def test_admission_denial_is_pre_dispatch_and_actionable(reason, code):
    probe, _, selected, sent, _ = stack(admission_failure=RuntimeError(reason))
    result = await probe.probe_real_capped(selected)
    assert result["error_code"] == code and result["dispatch_state"] == "not_dispatched"
    assert result["tokens_spent"] == 0 and not sent
    assert "Запрос не отправлен" in result["error"] and "PRIVATE_UNRECOGNIZED_SQL" not in str(result)


@pytest.mark.asyncio
async def test_accounting_failure_keeps_received_answer_and_json_without_second_http():
    probe, _, selected, sent, _ = stack(accounting_failure=True)
    result = await probe.probe_real_capped(selected)
    assert not result["ok"] and result["error_code"] == "accounting_failed"
    assert result["http_status"] == 200 and result["content"] == PAYLOAD["choices"][0]["message"]["content"]
    assert result["response_json"] == PAYLOAD and result["tokens_spent"] == 5
    assert "PRIVATE_ACCOUNTING" not in str(result) and len(sent) == 1


@pytest.mark.asyncio
async def test_non_json_response_remains_visible_and_escaped_by_template():
    probe, _, selected, sent, _ = stack(body="<html>not JSON fake-test-key</html>")
    result = await probe.probe_real_capped(selected)
    assert result["error_code"] == "invalid_response" and result["http_status"] == 200
    assert "not JSON" in result["response_body"] and "fake-test-key" not in str(result) and len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message", [{"content": None, "reasoning_content": "Thinking"}, {"content": "", "tool_calls": []}]
)
async def test_empty_text_is_not_confused_with_missing_api_response(message):
    payload = {"choices": [{"message": message, "finish_reason": "length"}]}
    probe, _, selected, sent, _ = stack(payload=payload)
    result = await probe.probe_real_capped(selected)
    assert result["error_code"] == "empty_content" and result["http_status"] == 200
    assert result["response_json"] == payload and result["finish_reason"] == "length" and len(sent) == 1


@pytest.mark.asyncio
async def test_200_error_envelope_is_not_success():
    probe, _, selected, sent, _ = stack(payload={"error": {"message": "provider returned error"}})
    result = await probe.probe_real_capped(selected)
    assert not result["ok"] and result["error_code"] == "provider_error" and len(sent) == 1


def test_safe_display_redacts_recursively_without_destroying_usage_or_evidence():
    probe, _ = probe_module()
    raw = {
        "authorization": "Bearer PRIVATE",
        "session_id": "PRIVATE_SESSION",
        "token": "PRIVATE_TOKEN",
        "nested": {"api_key": "secret", "cookie": "SESSION"},
        "content": "echo fake-test-key and Bearer another-secret access_token=query-secret",
        "usage": {"prompt_tokens": 4, "completion_tokens": 5},
    }
    displayed = probe.safe_probe_value(raw, "fake-test-key")
    assert displayed["usage"] == raw["usage"]
    assert all(
        value not in str(displayed)
        for value in ["PRIVATE", "SESSION", "fake-test-key", "another-secret", "query-secret"]
    )
    assert raw["nested"]["api_key"] == "secret"  # display copies never rewrite evidence
    assert "[truncated]" in str(probe.safe_probe_value({"content": "x" * 25000}))


@pytest.mark.asyncio
async def test_rejection_uses_real_template_context_and_original_http_status():
    probe, post = probe_module()
    actions, _, view = action_module(probe)
    view.templates.TemplateResponse = AsyncMock(
        side_effect=lambda req, name, context, **kw: NS(context=context, status_code=kw.get("status_code", 200))
    )
    reply = await actions.LLMModelActions.reject_test(
        view, NS(), "7", "question", code="workspace_inactive", text="Запрос не отправлен", status_code=422
    )
    assert reply.status_code == 422
    assert reply.context["result"]["dispatch_state"] == "not_dispatched"
    assert "PERSONAL_BILLING_ENABLED" not in reply.context["billing_block_reason"]
    post.assert_not_awaited()


def test_template_exposes_readable_answer_safe_json_and_statuses_without_checkbox():
    env = template_env()
    template = env.from_string(
        (ROOT / "app/templates/llm_model/test_results.html")
        .read_text()
        .replace('{% extends "sqladmin/layout.html" %}', "")
    )
    html = template.render(
        model=model(),
        provider=model().provider,
        status_checks=[],
        result=dict(
            ok=False,
            tier="real",
            text="accounting failed",
            content="<script>bad()</script>\nAnswer",
            response_json={"choices": [{"message": {"content": "<script>bad()</script>"}}]},
            dispatch_state="response_received",
            stage="accounting",
            http_status=200,
            finish_reason="stop",
            error_code="accounting_failed",
            tokens_spent=5,
        ),
        billing_block_reason="Рабочее пространство неактивно",
    )
    assert "<script>bad()" not in html and 'name="confirm_real"' not in html
    assert 'class="llm-answer-text"' in html and 'id="llm-response-json"' in html
    assert "Ответ API получен" in html and "HTTP-статус API" in html and "accounting_failed" in html
    assert " disabled" not in html and "PERSONAL_BILLING_ENABLED" not in html


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["2147483648", "99999999999999999999999"])
async def test_workspace_overflow_is_rendered_before_database_lookup(tenant):
    handler, request, lookup, submit, _, _ = endpoint_case(tenant=tenant)
    result = await handler(request)
    assert result.status_code == 422 and result.code == "workspace_required"
    lookup.assert_not_awaited()
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_probe_chat_keeps_existing_shape_and_diagnostics_out_of_payload():
    _, _, _, sent, client = stack()
    result = await client.chat([{"role": "user", "content": "hello"}])
    assert not hasattr(client, "_probe_diagnostics")
    assert result["raw"] == PAYLOAD and result["_personal_dispatch_id"] == "owned"
    import json

    payload = json.loads(sent[0].content)
    assert set(payload) == {"model", "messages", "temperature", "max_tokens", "stream"}


@pytest.mark.asyncio
async def test_admission_effective_token_limit_is_visible():
    probe, dependency, selected, _, _ = stack()

    async def admit(client, payload, **kwargs):
        payload["max_tokens"] = 32
        return {"dispatch_id": "owned"}, "owned"

    dependency._personal_admission = AsyncMock(side_effect=admit)
    actions, manager, view = action_module(probe)
    manager.get.return_value = selected
    result = (await actions.LLMModelActions.submit_test(view, NS(), "7", "real", "question"))["result"]
    assert result["max_tokens"] == 32


@pytest.mark.asyncio
async def test_provider_echoed_key_is_hidden_in_answer_json_and_formatted_json():
    raw = {
        "choices": [{"message": {"content": "fake-test-key <script>bad()</script>"}}],
        "metadata": {"authorization": "Bearer unrelated-secret"},
    }
    probe, _, selected, _, _ = stack(payload=raw)
    result = await probe.probe_real_capped(selected)
    assert result["ok"] and "fake-test-key" not in str(result) and "unrelated-secret" not in str(result)
    assert raw["choices"][0]["message"]["content"].startswith("fake-test-key")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], [1, 2], "unexpected"])
async def test_non_object_json_is_response_format_failure_not_accounting_failure(payload):
    probe, _, selected, sent, _ = stack(payload=payload)
    result = await probe.probe_real_capped(selected)
    assert result["error_code"] == "invalid_response" and result["stage"] == "response"
    assert result["response_json"] == payload and result["http_status"] == 200 and len(sent) == 1
