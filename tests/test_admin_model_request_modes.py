"""No-DB regression: explicit HTTP/client doubles only, never live API calls."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from source_import_isolation import load_isolated_source
from test_admin_model_billing_integration import endpoint_case
from test_admin_model_probe import ROOT, action_module, model, probe_module, template_env


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,expected_limit", [("", 8), (" \n ", 8), ("Что такое API?", 512)])
async def test_prompt_or_default_reaches_billed_client_once(prompt, expected_limit):
    module, post = probe_module({"choices": [{"message": {"content": "ответ"}}]})
    result = await module.probe_real_capped(model(), prompt=prompt)
    assert result["ok"]
    payload = post.await_args.kwargs["json"]
    assert payload["messages"] == [{"role": "user", "content": prompt.strip() or module.PROBE_PROMPT}]
    assert payload["max_tokens"] == expected_limit
    assert post.await_count == 1
    journal = module.__isolated_imports__["app.services.ai.llm_client"].journal_chat_attempt
    journal.assert_awaited_once()


@pytest.mark.parametrize("limit,expected", [(4, 4), (128, 128), (2048, 512)])
def test_answer_does_not_exceed_model_limit(limit, expected):
    module, _ = probe_module()
    assert module.request_parameters(model(max_tokens=limit), "question") == ("question", expected)


@pytest.mark.asyncio
async def test_real_prompt_length_refused_before_factory():
    module, post = probe_module()
    factory = module.__isolated_imports__["app.services.ai.llm_client"].LLMClientFactory
    factory.create = Mock(side_effect=AssertionError("No transport"))
    with pytest.raises(ValueError, match="8000"):
        await module.probe_real_capped(model(), prompt="x" * 8001)
    factory.create.assert_not_called()
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_custom_request_never_renders_success_text_or_retries():
    module, post = probe_module({}, status=503)
    actions, _, view = action_module(module)
    result = (await actions.LLMModelActions.submit_test(view, NS(), "7", "real", "question"))["result"]
    assert not result["ok"] and "Получен ответ" not in result["text"]
    assert "автоматически" in result["text"]
    assert result["request_prompt"] == "question" and result["max_tokens"] == 512
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_prompt_is_forwarded_by_actual_endpoint_body_inside_trusted_scopes():
    handler, request, _, submit, seen, form = endpoint_case()
    form["prompt"] = "Мой настоящий вопрос"
    assert await handler(request) == "rendered"
    assert submit.await_args.args[-1] == "Мой настоящий вопрос"
    assert seen == ["configure", ("tenant", 3), ("actor", 31)]


def connectivity_module(*, status=200, data=None, error=None):
    sent = []
    selected = model()

    def transport(request):
        sent.append(request)
        assert request.method == "GET" and str(request.url) == "https://provider.invalid/v1/models"
        assert request.headers["Authorization"] == "Bearer fake-key"
        assert not request.content
        assert all(value == 20 for value in request.extensions["timeout"].values())
        if error:
            raise error
        return httpx.Response(status, json=data if data is not None else {"data": [{"id": selected.model_id}]})

    client = NS(
        base_url="https://provider.invalid/v1", _headers=lambda: {"Authorization": "Bearer fake-key"}, chat=AsyncMock()
    )
    factory = NS(create=Mock(return_value=client))
    real_async_client = httpx.AsyncClient
    http = NS(
        AsyncClient=lambda **kw: real_async_client(transport=httpx.MockTransport(transport), **kw),
        TimeoutException=httpx.TimeoutException,
    )
    module = load_isolated_source(
        "_metadata_probe",
        ROOT / "app/services/ai/model_probe.py",
        {"app.services.ai.llm_client": NS(LLMClientFactory=factory)},
        module_imports={"httpx": http},
    )
    return module, selected, sent, client, factory


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [True, False])
async def test_short_is_one_authenticated_metadata_request_without_generation(listed):
    module, selected, sent, client, _ = connectivity_module(
        data={"data": [{"id": "example/model" if listed else "other"}]}
    )
    result = await module.probe_connectivity(selected)
    assert result["ok"] and result["tokens_spent"] == 0
    assert result["http_status"] == 200 and result["model_available"] is listed
    assert result["elapsed_ms"] >= 0 and len(sent) == 1
    client.chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 403, 404, 405, 429, 500])
async def test_metadata_failures_have_safe_status_and_no_fallback(status):
    module, selected, sent, client, _ = connectivity_module(status=status, data={"secret": "PRIVATE_BODY"})
    result = await module.probe_connectivity(selected)
    assert not result["ok"] and result["http_status"] == status
    assert "PRIVATE_BODY" not in str(result) and "fake-key" not in str(result)
    assert len(sent) == 1
    client.chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{}, {"data": "bad"}, {"data": [None]}, {"data": [{"id": 1}]}])
async def test_bad_metadata_is_not_api_success(data):
    module, selected, sent, client, _ = connectivity_module(data=data)
    assert not (await module.probe_connectivity(selected))["ok"]
    assert len(sent) == 1
    client.chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ReadTimeout("PRIVATE"), httpx.ConnectError("PRIVATE"), ValueError("PRIVATE")])
async def test_metadata_timeout_or_transport_error_has_no_secret_or_retry(error):
    module, selected, sent, client, _ = connectivity_module(error=error)
    result = await module.probe_connectivity(selected)
    assert not result["ok"] and "PRIVATE" not in str(result)
    assert len(sent) == 1
    client.chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["inactive_model", "inactive_provider", "missing_key", "anthropic"])
async def test_metadata_bad_configuration_has_no_http(change):
    module, selected, sent, _, factory = connectivity_module()
    if change == "inactive_model":
        selected.is_active = False
    elif change == "inactive_provider":
        selected.provider.is_active = False
    elif change == "missing_key":
        selected.provider.get_api_key = lambda: ""
    else:
        selected.provider.api_format = "anthropic"
    assert not (await module.probe_connectivity(selected))["ok"]
    assert not sent
    factory.create.assert_not_called()


@pytest.mark.asyncio
async def test_old_failure_does_not_permanently_block_recovery_request():
    module, selected, sent, _, _ = connectivity_module()
    selected.last_error_at = "old failure"
    assert (await module.probe_connectivity(selected))["ok"] and len(sent) == 1
    real, post = probe_module({"choices": [{"message": {"content": "да"}}]})
    assert (await real.probe_real_capped(selected))["ok"] and post.await_count == 1


@pytest.mark.parametrize(
    "tier,label",
    [("connectivity", "Короткая проверка · API"), ("mock", "Mock · без API"), ("real", "Реальный запрос · API")],
)
def test_mode_specific_output_escapes_prompts_and_shows_diagnostics(tier, label):
    env = template_env()
    template = env.from_string(
        (ROOT / "app/templates/llm_model/test_results.html")
        .read_text()
        .replace('{% extends "sqladmin/layout.html" %}', "")
    )
    result = dict(ok=True, tier=tier, text="OK", elapsed_ms=17, tokens_spent=0)
    if tier == "connectivity":
        result.update(method="GET /models", http_status=200, model_available=False, models_count=2)
    else:
        result.update(request_prompt="<script>bad()</script>", content="<script>bad()</script>")
    if tier == "mock":
        result["usage"] = {"prompt_tokens": 8, "completion_tokens": 12}
    if tier == "real":
        result.update(max_tokens=512, estimated_cost=None)
    html = template.render(
        model=model(), provider=model().provider, result=result, prompt="", probe_prompt="да", status_checks=[]
    )
    assert label in html and "17 мс" in html and "<script>bad()" not in html
    assert "Реальная проба" not in html
    if tier == "connectivity":
        assert "GET /models" in html and "200" in html and "не подтверждена" in html
    if tier == "mock":
        assert "имитация" in html and "вымышленный" in html
    if tier == "real":
        assert "512 токенов" in html and "Отправленное сообщение" in html
