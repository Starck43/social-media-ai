from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.applications import Starlette
from starlette.requests import Request

import app.services.ai.llm_client as llm_client
import app.services.ai.personal_dispatch as personal_dispatch
from app.core.config import settings
from app.core.personal_catalog_context import catalog_execution_scope
from app.models.managers.personal_billing_manager import PersonalBillingUnavailable
from app.services.ai.budget_admission import BudgetAdmissionDenied
from app.services.ai.llm_client import CustomClient, OpenAICompatibleClient, chat_with_fallback

# Legacy catalog owner seeded by the personal catalog ownership migration.
CATALOG_OWNER_ID = 2
_REAL_REVALIDATE_PERSONAL_DISPATCH = llm_client._revalidate_personal_dispatch_before_http


@pytest.fixture(autouse=True)
def _skip_personal_dispatch_revalidation(monkeypatch):
    """Keep unrelated transport tests isolated from catalog persistence."""
    monkeypatch.setattr(llm_client, "_revalidate_personal_dispatch_before_http", AsyncMock())


@pytest.fixture(autouse=True)
def _catalog_owner():
    """Clients assert the model belongs to the acting personal catalog."""
    with catalog_execution_scope(CATALOG_OWNER_ID):
        yield


def model(model_id: int = 1):
    provider = SimpleNamespace(
        id=20 + model_id,
        name=f"provider-{model_id}",
        api_format="openai",
        is_active=True,
        base_url="https://example.invalid/v1",
        auth_header=None,
        get_api_key=lambda: "test-key",
        owner_user_id=CATALOG_OWNER_ID,
    )
    return SimpleNamespace(
        id=model_id,
        name=f"model-{model_id}",
        model_id=f"remote-{model_id}",
        provider=provider,
        is_active=True,
        capabilities=["text"],
        max_tokens=64,
        default_temperature=0.0,
        input_cost_per_1k=1,
        output_cost_per_1k=1,
        owner_user_id=CATALOG_OWNER_ID,
    )


def custom_model(model_id: int = 1):
    provider = SimpleNamespace(
        id=20 + model_id,
        name=f"custom-provider-{model_id}",
        api_format="custom",
        is_active=True,
        base_url="https://example.invalid/v1",
        auth_header=None,
        get_api_key=lambda: "test-key",
        owner_user_id=CATALOG_OWNER_ID,
    )
    return SimpleNamespace(
        id=model_id,
        name=f"custom-model-{model_id}",
        model_id=f"remote-custom-{model_id}",
        provider=provider,
        is_active=True,
        capabilities=["text"],
        custom_endpoint_path="/generate",
        max_tokens=64,
        default_temperature=0.0,
        input_cost_per_1k=1,
        output_cost_per_1k=1,
        owner_user_id=CATALOG_OWNER_ID,
    )


@pytest.mark.asyncio
async def test_custom_chat_success_records_response_received_and_preserves_shape(monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    client = CustomClient(custom_model())
    monkeypatch.setattr(client, "_rate_limit", AsyncMock())
    posts = AsyncMock()
    provider_payload = {
        "content": "ack",
        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_cost": "0.000001"},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=provider_payload))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: _CountingAsyncClient(real_client, posts, transport=transport, **kwargs),
    )
    receipt = {"dispatch_id": "a" * 32, "utc_day": datetime.now(timezone.utc).date()}
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(receipt, receipt["dispatch_id"])))
    observe = AsyncMock()
    monkeypatch.setattr(llm_client, "_observe_personal_outcome", observe)
    record = AsyncMock()
    monkeypatch.setattr(llm_client, "_record_llm_usage", record)

    response = await client.chat([{"role": "user", "content": "hello"}])

    assert posts.await_count == 1
    assert response["content"] == "ack"
    assert response["tool_calls"] == []
    assert response["_personal_dispatch_id"] == receipt["dispatch_id"]
    assert response["finish_reason"] is None
    assert response["raw"] == provider_payload
    assert response["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.000001,
        "cost_source": "provider",
        "cost_is_estimate": False,
    }
    observe.assert_awaited_once_with(
        receipt,
        dispatch_state="response_received",
        outcome="succeeded",
        accounting_envelope={
            "accounting_usage": {"prompt_tokens": 4, "completion_tokens": 2},
            "accounting_cache_usage": {
                "format": "unknown",
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "cache_write_5m_tokens": None,
                "cache_write_1h_tokens": None,
            },
            "accounting_cost_usd": "0.000001",
            "accounting_cost_reported": True,
        },
    )
    record.assert_awaited_once_with(client.model, success=True, usage=response["usage"])


@pytest.mark.asyncio
async def test_custom_chat_transport_failure_records_possible_unknown_and_reraises(monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    client = CustomClient(custom_model())
    monkeypatch.setattr(client, "_rate_limit", AsyncMock())
    posts = AsyncMock()

    def raise_transport(request):
        raise httpx.ConnectError("upstream refused")

    transport = httpx.MockTransport(raise_transport)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: _CountingAsyncClient(real_client, posts, transport=transport, **kwargs),
    )
    receipt = {"dispatch_id": "a" * 32, "utc_day": datetime.now(timezone.utc).date()}
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(receipt, receipt["dispatch_id"])))
    observe = AsyncMock()
    monkeypatch.setattr(llm_client, "_observe_personal_outcome", observe)
    record = AsyncMock()
    monkeypatch.setattr(llm_client, "_record_llm_usage", record)

    with pytest.raises(httpx.ConnectError) as error:
        await client.chat([{"role": "user", "content": "hello"}])

    assert error.value.personal_dispatch_id == receipt["dispatch_id"]
    assert posts.await_count == 1
    observe.assert_awaited_once_with(receipt, dispatch_state="possible", outcome="unknown")
    record.assert_awaited_once_with(client.model, success=False)


@pytest.mark.asyncio
async def test_denied_admission_sends_no_provider_request(monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    client = OpenAICompatibleClient(model())
    monkeypatch.setattr(client, "_rate_limit", AsyncMock())
    posts = AsyncMock()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: _CountingAsyncClient(real_client, posts, **kwargs),
    )

    async def denied(*args, **kwargs):
        raise BudgetAdmissionDenied("daily cap")

    monkeypatch.setattr(llm_client, "_personal_admission", denied)
    with pytest.raises(BudgetAdmissionDenied):
        await client.chat([{"role": "user", "content": "hello"}])
    posts.assert_not_awaited()


@pytest.mark.asyncio
async def test_outcome_write_failure_after_response_does_not_repeat_http(monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    client = OpenAICompatibleClient(model())
    monkeypatch.setattr(client, "_rate_limit", AsyncMock())
    posts = AsyncMock()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: _CountingAsyncClient(real_client, posts, transport=transport, **kwargs),
    )
    receipt = {"dispatch_id": "a" * 32, "utc_day": datetime.now(timezone.utc).date()}
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(receipt, receipt["dispatch_id"])))

    async def fail_settlement(*args, **kwargs):
        raise PersonalBillingUnavailable("commit acknowledgement missing")

    monkeypatch.setattr(llm_client, "_observe_personal_outcome", fail_settlement)
    monkeypatch.setattr(llm_client, "_record_llm_usage", AsyncMock())
    with pytest.raises(PersonalBillingUnavailable):
        await client.chat([{"role": "user", "content": "hello"}])
    assert posts.await_count == 1


@pytest.mark.asyncio
async def test_prior_day_reservation_records_not_dispatched_and_sends_no_http(monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    client = OpenAICompatibleClient(model())
    monkeypatch.setattr(client, "_rate_limit", AsyncMock())
    posts = AsyncMock()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: _CountingAsyncClient(real_client, posts, **kwargs),
    )
    prior_day = datetime.now(timezone.utc).date() - timedelta(days=1)
    receipt = {"dispatch_id": "a" * 32, "utc_day": prior_day}
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(receipt, receipt["dispatch_id"])))
    release = AsyncMock(return_value=None)
    monkeypatch.setattr(llm_client, "_observe_personal_failure", release)
    with pytest.raises(PersonalBillingUnavailable):
        await client.chat([{"role": "user", "content": "hello"}])
    posts.assert_not_awaited()
    release.assert_awaited_once_with(receipt, not_dispatched=True)


@pytest.mark.asyncio
async def test_credential_revoked_after_admission_releases_reservation_without_http(monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    client = OpenAICompatibleClient(model())
    monkeypatch.setattr(client, "_rate_limit", AsyncMock())
    posts = AsyncMock()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: _CountingAsyncClient(real_client, posts, **kwargs),
    )
    receipt = {"dispatch_id": "b" * 32, "utc_day": datetime.now(timezone.utc).date()}
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(receipt, receipt["dispatch_id"])))
    release = AsyncMock(return_value=None)
    monkeypatch.setattr(llm_client, "_observe_personal_failure", release)
    monkeypatch.setattr(llm_client, "_revalidate_personal_dispatch_before_http", _REAL_REVALIDATE_PERSONAL_DISPATCH)
    monkeypatch.setattr(
        personal_dispatch,
        "validate_personal_dispatch",
        AsyncMock(side_effect=PermissionError("Provider configuration changed; reload before dispatch")),
    )

    with pytest.raises(PermissionError, match="Provider configuration changed"):
        await client.chat([{"role": "user", "content": "hello"}])

    posts.assert_not_awaited()
    release.assert_awaited_once_with(receipt, not_dispatched=True)


@pytest.mark.asyncio
async def test_fallback_does_not_try_next_model_after_billing_denial(monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    candidates = [model(1), model(2)]
    monkeypatch.setattr(llm_client, "_llm_models", AsyncMock(return_value=candidates))
    monkeypatch.setattr(llm_client, "_allowed_model_types", AsyncMock(return_value=None))
    posts = AsyncMock()
    client = OpenAICompatibleClient(candidates[0])
    monkeypatch.setattr(client, "_rate_limit", AsyncMock())

    async def denied(*args, **kwargs):
        raise BudgetAdmissionDenied("daily cap")

    monkeypatch.setattr(llm_client, "_personal_admission", denied)
    monkeypatch.setattr(llm_client.LLMClientFactory, "create", lambda selected: client)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kwargs: _CountingAsyncClient(real_client, posts, **kwargs),
    )
    with pytest.raises(BudgetAdmissionDenied):
        await chat_with_fallback([{"role": "user", "content": "hello"}])
    posts.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_probe_post_requires_csrf_before_any_authenticated_probe(monkeypatch):
    from app.admin import endpoints

    app = Starlette()
    app.state.csrf_manager = SimpleNamespace(verify_token=lambda _token: False)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/admin/llm-model/test-real",
        "raw_path": b"/admin/llm-model/test-real",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("test", 443),
        "app": app,
        "session": {"token": "ignored"},
    }
    request = Request(scope)
    posts = AsyncMock()
    monkeypatch.setattr(endpoints, "get_authenticated_user", AsyncMock())
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    with pytest.raises(HTTPException) as error:
        await endpoints.llm_model_test_real(request, "1", "hello", 7, "invalid")
    assert error.value.status_code == 403
    endpoints.get_authenticated_user.assert_not_awaited()
    posts.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_probe_post_rejects_non_admin_before_probe(monkeypatch):
    from app.admin import endpoints
    from app.models import User

    app = Starlette()
    app.state.csrf_manager = SimpleNamespace(verify_token=lambda _token: True)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/admin/llm-model/test-real",
        "raw_path": b"/admin/llm-model/test-real",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("test", 443),
        "app": app,
        "session": {"token": "valid"},
    }
    request = Request(scope)
    actor = SimpleNamespace(id=31, is_active=True, is_platform_admin=False)
    query = SimpleNamespace(get=AsyncMock(return_value=actor))
    objects = SimpleNamespace(prefetch_related=lambda *_args: query)
    monkeypatch.setattr(User, "objects", objects)
    monkeypatch.setattr(endpoints, "get_authenticated_user", AsyncMock(return_value=actor))
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    posts = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await endpoints.llm_model_test_real(request, "1", "hello", 7, "valid")
    assert error.value.status_code == 403
    posts.assert_not_awaited()


class _CountingAsyncClient:
    def __init__(self, factory, posts, **kwargs):
        self.client = factory(**kwargs)
        self.posts = posts

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def post(self, *args, **kwargs):
        await self.posts(*args, **kwargs)
        return await self.client.post(*args, **kwargs)
