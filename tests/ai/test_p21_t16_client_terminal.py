"""Client terminal settlement handoff using the P21-T12 mocked dispatch harness."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.tenant_context import tenant_scope
from app.services.ai import dispatch_seam as seam
from app.services.ai import llm_client
from tests.ai import test_p21_t12_dispatch_seam as t12

__source_only__ = True
_REAL_REVALIDATE = llm_client._revalidate_personal_dispatch_before_http


@pytest.fixture
def strict_terminal(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DISPATCH_SEAM_ENABLED", True)
    monkeypatch.setattr("app.core.personal_catalog_context.current_catalog_owner_id", lambda: t12.OWNER)
    monkeypatch.setattr("app.core.tenant_context.current_tenant_id", lambda: t12.TENANT)
    settlement = t12.Settlement()
    foundation = t12.full_foundation(settlement=settlement)
    monkeypatch.setattr(seam, "_compat_foundation", lambda: foundation)
    monkeypatch.setattr(seam, "_compat_context", lambda client, request: t12.strict_context(credential_version=3))
    terminal = AsyncMock()
    monkeypatch.setattr(seam, "settle", terminal)
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(llm_client, "_record_llm_usage", AsyncMock())
    monkeypatch.setattr(llm_client, "_verify_personal_dispatch_day", AsyncMock())
    monkeypatch.setattr(llm_client, "_revalidate_personal_dispatch_before_http", AsyncMock())
    return terminal


class ResponseTransport:
    def __init__(self, response):
        self.response = response

    async def post(self, url, **kwargs):
        return httpx.Response(200, json=self.response, request=httpx.Request("POST", url))


def install_transport(monkeypatch, transport):
    @asynccontextmanager
    async def provider_client():
        yield transport

    monkeypatch.setattr(llm_client, "provider_http_client", provider_client)


def install_success_response(monkeypatch):
    transport = ResponseTransport(
        {
            "choices": [{"message": {"content": '{"analysis": "ok"}'}}],
            "content": [{"type": "text", "text": '{"analysis": "ok"}'}],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "input_tokens": 3,
                "output_tokens": 2,
                "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "cache_read_input_tokens": 0,
            },
        }
    )
    install_transport(monkeypatch, transport)
    return transport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api_format,client_class",
    [
        ("openai", llm_client.OpenAICompatibleClient),
        ("anthropic", llm_client.AnthropicClient),
        ("custom", llm_client.CustomClient),
    ],
)
@pytest.mark.parametrize("method", ["chat", "analyze"])
async def test_success_passes_only_accounting_envelope_to_settlement(
    monkeypatch, strict_terminal, api_format, client_class, method
):
    install_success_response(monkeypatch)
    model = t12.make_model(api_format=api_format)
    model.provider.credential_version = 3
    client = client_class(model)
    client._rate_limit = AsyncMock()

    with catalog_execution_scope(t12.OWNER), tenant_scope(t12.TENANT):
        if method == "chat":
            await client.chat([{"role": "user", "content": "private prompt"}])
        else:
            await client.analyze("private prompt")

    strict_terminal.assert_awaited_once()
    admission = strict_terminal.await_args.args[0]
    assert admission.reserved is True
    assert strict_terminal.await_args.kwargs["outcome"] == "succeeded"
    evidence = strict_terminal.await_args.kwargs["evidence"]
    assert set(evidence) == {
        "accounting_usage",
        "accounting_cache_usage",
        "accounting_cost_usd",
        "accounting_cost_reported",
    }
    assert "private prompt" not in repr(evidence)
    assert "private-provider-body" not in repr(evidence)


@pytest.mark.asyncio
async def test_invalid_output_is_settled_as_failed_output(monkeypatch, strict_terminal):
    transport = install_success_response(monkeypatch)
    transport.response = {
        "choices": [{"message": {"content": '{"analysis": "ok"}'}}],
        "error": {"type": "synthetic_provider_error"},
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    model = t12.make_model(api_format="openai")
    model.provider.credential_version = 3
    client = llm_client.OpenAICompatibleClient(model)
    client._rate_limit = AsyncMock()

    with catalog_execution_scope(t12.OWNER), tenant_scope(t12.TENANT):
        await client.analyze("private prompt")

    assert strict_terminal.await_args.kwargs["outcome"] == "failed_output"


@pytest.mark.asyncio
async def test_transport_error_is_settled_without_provider_evidence(monkeypatch, strict_terminal):
    class FailingTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            raise httpx.TimeoutException("synthetic transport timeout")

    install_transport(monkeypatch, FailingTransport())
    model = t12.make_model(api_format="openai")
    model.provider.credential_version = 3
    client = llm_client.OpenAICompatibleClient(model)
    client._rate_limit = AsyncMock()

    with catalog_execution_scope(t12.OWNER), tenant_scope(t12.TENANT):
        with pytest.raises(httpx.TimeoutException, match="synthetic transport timeout"):
            await client.chat([{"role": "user", "content": "private prompt"}])

    assert strict_terminal.await_args.kwargs == {"outcome": "failed_transport", "evidence": None}


@pytest.mark.asyncio
async def test_cancelled_request_retains_unknown_liability(monkeypatch, strict_terminal):
    class CancelledTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            raise asyncio.CancelledError()

    install_transport(monkeypatch, CancelledTransport())
    model = t12.make_model(api_format="openai")
    model.provider.credential_version = 3
    client = llm_client.OpenAICompatibleClient(model)
    client._rate_limit = AsyncMock()

    with catalog_execution_scope(t12.OWNER), tenant_scope(t12.TENANT):
        with pytest.raises(asyncio.CancelledError):
            await client.chat([{"role": "user", "content": "private prompt"}])

    assert strict_terminal.await_args.kwargs == {"outcome": "unknown", "evidence": None}


@pytest.mark.asyncio
async def test_stale_credential_settles_as_not_dispatched(monkeypatch, strict_terminal):
    install_success_response(monkeypatch)
    monkeypatch.setattr(llm_client, "_revalidate_personal_dispatch_before_http", _REAL_REVALIDATE)
    model = t12.make_model(api_format="openai")
    model.provider.credential_version = 3
    client = llm_client.OpenAICompatibleClient(model)
    client._rate_limit = AsyncMock()
    client.credential_version = 4

    with catalog_execution_scope(t12.OWNER), tenant_scope(t12.TENANT):
        with pytest.raises(PermissionError, match="Credential configuration changed"):
            await client.chat([{"role": "user", "content": "private prompt"}])

    assert strict_terminal.await_args.kwargs == {"outcome": "not_dispatched", "evidence": None}
