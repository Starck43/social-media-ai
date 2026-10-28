"""Isolated combined-lane contracts; not application/DB acceptance.

Every application import below is replaced by an explicit local double. HTTP
uses MockTransport only. No settings, model registry, conftest or app startup.
"""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from tests.shared.decomposition_source_loader import load_decomposed_source as load_isolated_source
from test_admin_model_probe import COST_EVIDENCE, ROOT, extracted_handler, model, probe_module, template_env

from app.core.personal_catalog_context import catalog_execution_scope


@pytest.mark.asyncio
async def test_single_owner_mode_runs_without_personal_billing():
    module, post = probe_module({"choices": [{"message": {"content": "да"}}]})
    module.__isolated_imports__["app.core.config"].settings.PERSONAL_BILLING_ENABLED = False
    result = await module.probe_real_capped(model())
    assert result["ok"] and result["content"] == "да"
    post.assert_awaited_once()


@pytest.mark.asyncio
async def test_billed_probe_preserves_unknown_raw_usage_despite_legacy_zero_projection():
    module, _ = probe_module()
    dependency = module.__isolated_imports__["app.services.ai.llm_client"]
    client = NS(
        api_key="fake",
        chat=AsyncMock(
            return_value={
                "content": "да",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "raw": {},
                "_personal_dispatch_id": "owned-dispatch",
            }
        ),
    )
    dependency.LLMClientFactory.create = lambda selected: client
    result = await module.probe_real_capped(model())
    assert result["ok"] and result["tokens_spent"] is None
    assert result["usage"]["prompt_tokens"] is None
    assert client.timeout == 20
    assert client.chat.await_args.args == ([{"role": "user", "content": module.PROBE_PROMPT}],)
    assert client.chat.await_args.kwargs == {
        "max_tokens": 8,
        "temperature": 0.0,
        "personal_billing_purpose": "admin_probe",
    }
    assert dependency.journal_chat_attempt.await_args.kwargs["dispatch_id"] == "owned-dispatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("PRIVATE_SECRET"), TimeoutError("PRIVATE_SECRET")])
async def test_accounting_or_transport_failure_never_retries_or_exposes_exception(error):
    module, post = probe_module()
    dependency = module.__isolated_imports__["app.services.ai.llm_client"]
    client = NS(api_key="fake", chat=AsyncMock(side_effect=error))
    dependency.LLMClientFactory.create = lambda selected: client
    result = await module.probe_real_capped(model())
    assert not result["ok"] and result["tokens_spent"] is None
    assert "PRIVATE_SECRET" not in str(result)
    assert client.chat.await_count == 1
    dependency.journal_chat_attempt.assert_not_awaited()
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_secondary_journal_failure_retains_answer_and_never_replays():
    module, post = probe_module({"choices": [{"message": {"content": "да"}}]})
    dependency = module.__isolated_imports__["app.services.ai.llm_client"]
    dependency.journal_chat_attempt.side_effect = RuntimeError("PRIVATE_SECRET")
    result = await module.probe_real_capped(model())
    assert result["ok"] and result["content"] == "да" and result["tokens_spent"] is None
    assert "save_warning" in result and "PRIVATE_SECRET" not in str(result)
    assert post.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,limit", [("", 8), ("What is an API?", 512)])
async def test_actual_chat_transport_keeps_caps_and_admission_but_not_timeout_in_json(prompt, limit):
    settings = NS(PERSONAL_BILLING_ENABLED=True, LLM_DEFAULT_TIMEOUT=60)
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "да"}}]})

    transport_double = NS(
        AsyncClient=lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        HTTPStatusError=httpx.HTTPStatusError,
        TimeoutException=httpx.TimeoutException,
        TransportError=httpx.TransportError,
    )
    client_module = load_isolated_source(
        "_billed_probe_transport",
        ROOT / "app/services/ai/llm_client.py",
        {
            "app.core.config": NS(settings=settings),
            "app.models": NS(LLMModel=object),
            "app.services.ai.json_schema_builder": NS(validate_with_pydantic=Mock()),
            "app.services.ai.attempt_journal": NS(provider_cost_evidence=lambda data: (False, None)),
            "app.services.billing.errors": NS(is_personal_billing_block=lambda error: False),
        },
        module_imports={"httpx": transport_double},
    )
    client_module._personal_admission = AsyncMock(return_value=({"dispatch_id": "owned"}, "owned"))
    client_module._verify_personal_dispatch_day = AsyncMock()
    # P3 credential revalidation owns the pre-HTTP DB check; this harness doubles it.
    client_module._revalidate_personal_dispatch_before_http = AsyncMock()
    client_module._observe_personal_outcome = AsyncMock()
    client_module._record_llm_usage = AsyncMock()
    selected = model()
    selected.provider.base_url, selected.provider.auth_header = "https://provider.invalid/v1", None
    # The actual client asserts the bound personal-catalogue owner at construction.
    with catalog_execution_scope(selected.owner_user_id):
        client = client_module.OpenAICompatibleClient(selected)
    client._rate_limit = AsyncMock()
    client_module.LLMClientFactory.create = lambda selected: client
    client_module.journal_chat_attempt = AsyncMock()
    probe = load_isolated_source(
        "_billed_probe_service",
        ROOT / "app/services/ai/model_probe.py",
        {
            "app.core.config": NS(settings=settings),
            "app.services.ai.llm_client": client_module,
            "app.services.ai.attempt_journal": COST_EVIDENCE,
        },
    )
    result = await probe.probe_real_capped(selected, prompt=prompt)
    assert result["ok"] and result["tokens_spent"] is None and len(sent) == 1
    import json

    payload = json.loads(sent[0].content)
    assert payload["max_tokens"] == limit and payload["messages"][0]["content"] == (prompt or probe.PROBE_PROMPT)
    assert "timeout" not in payload and "personal_billing_purpose" not in payload
    assert all(value == 20 for value in sent[0].extensions["timeout"].values())
    assert client_module._personal_admission.await_args.kwargs["purpose"] == "admin_probe"
    client_module._observe_personal_outcome.assert_awaited_once()


def endpoint_case(*, enabled=True, admin=True, csrf=True, consent=True, tenant="3", active=True, allowed=True):
    user = NS(id=31, is_platform_admin=admin)
    seen = []

    async def guard(*args):
        seen.append("configure")
        if not allowed:
            raise HTTPException(403, "denied")
        return user

    @contextmanager
    def tenant_scope(identity):
        seen.append(("tenant", identity))
        yield

    @contextmanager
    def permission_scope(actor):
        assert actor is user
        seen.append(("actor", actor.id))
        yield

    lookup = AsyncMock(return_value=NS(id=3, is_active=active))
    submit = AsyncMock(return_value="rendered")
    submit.reject = AsyncMock(side_effect=lambda *a, **kw: NS(status_code=kw["status_code"], code=kw["code"]))
    handler = extracted_handler(
        "app/admin/endpoints.py",
        "submit_llm_model_test",
        {
            "require_admin_perm": guard,
            "ActionType": NS(CONFIGURE="configure"),
            "HTTPException": HTTPException,
            "settings": NS(PERSONAL_BILLING_ENABLED=enabled),
            "Tenant": NS(objects=NS(get=lookup)),
            "get_csrf_manager": lambda request: NS(verify_token=lambda token: csrf),
        },
        {
            "app.admin.actions": NS(LLMModelActions=NS(submit_test=submit, reject_test=submit.reject)),
            "app.core.permissions": NS(permission_scope=permission_scope),
            "app.core.tenant_context": NS(tenant_scope=tenant_scope),
        },
    )
    form = dict(
        model_id="7",
        test_type="real",
        prompt="UNTRUSTED_PROMPT",
        csrf_token="fake",
        confirm_real="yes" if consent else "",
        tenant_id=tenant,
        payer_user_id="999",
    )
    request = NS(form=AsyncMock(return_value=form), state=NS(), app=NS(state=NS(admin="view")))
    return handler, request, lookup, submit, seen, form


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,status",
    [
        ({"allowed": False}, 403),
        ({"csrf": False}, 403),
        ({"admin": False}, 403),
        ({"tenant": ""}, 422),
        ({"tenant": "3,4"}, 422),
        ({"tenant": "0"}, 422),
        ({"active": False}, 422),
    ],
)
async def test_paid_post_refuses_before_dispatch(changes, status):
    handler, request, lookup, submit, seen, _ = endpoint_case(**changes)
    if changes.get("allowed") is False or changes.get("csrf") is False:
        with pytest.raises(HTTPException) as error:
            await handler(request)
        assert error.value.status_code == status
        submit.reject.assert_not_awaited()
    else:
        response = await handler(request)
        assert response.status_code == status
        submit.reject.assert_awaited_once()
    submit.assert_not_awaited()
    assert not any(isinstance(item, tuple) for item in seen)
    if not changes.get("active") is False:
        lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_post_uses_trusted_actor_and_explicit_workspace_not_form_payer():
    handler, request, lookup, submit, seen, _ = endpoint_case()
    assert await handler(request) == "rendered"
    assert seen == ["configure", ("tenant", 3), ("actor", 31)]
    lookup.assert_awaited_once_with(id=3)
    assert submit.await_args.args == ("view", request, "7", "real", "UNTRUSTED_PROMPT")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["short", "mock"])
async def test_non_generation_modes_do_not_require_billing_tenant_or_payer(mode):
    handler, request, lookup, submit, seen, form = endpoint_case(enabled=False, admin=False, consent=False, tenant="")
    form["test_type"] = mode
    assert await handler(request) == "rendered"
    # The actor scope binds the personal catalogue; the tenant stays out of scope.
    assert seen == ["configure", ("actor", 31)]
    lookup.assert_not_awaited()
    assert submit.await_args.args[3] == mode


@pytest.mark.asyncio
async def test_retired_paid_route_never_translates_or_dispatches():
    auth = AsyncMock(return_value=NS(id=31))
    lookup = AsyncMock(return_value=NS(is_active=True, is_platform_admin=True))
    handler = extracted_handler(
        "app/admin/endpoints.py",
        "llm_model_test_real",
        {
            "Form": lambda *a, **k: None,
            "HTTPException": HTTPException,
            "get_csrf_manager": lambda request: NS(verify_token=lambda token: True),
            "get_authenticated_user": auth,
            "User": NS(objects=NS(prefetch_related=lambda *a: NS(get=lookup))),
        },
        {},
    )
    with pytest.raises(HTTPException) as error:
        await handler(NS(session={"token": "fake"}), "7", "untrusted", 3, "fake")
    assert error.value.status_code == 410


def test_billing_workspace_selector_is_escaped_optional_for_local_modes():
    env = template_env()
    template = env.from_string(
        (ROOT / "app/templates/llm_model/test_results.html")
        .read_text()
        .replace('{% extends "sqladmin/layout.html" %}', "")
    )
    html = template.render(
        model=model(),
        provider=model().provider,
        prompt="",
        result=None,
        status_checks=[],
        billing_ready=True,
        billing_tenants=[NS(id=3, name="<script>bad()</script>")],
        billing_tenant_id=None,
    )
    assert "<script>bad()</script>" not in html and 'name="tenant_id"' in html
    assert 'name="payer_user_id"' not in html
    assert 'name="tenant_id" required' not in html


@pytest.mark.asyncio
async def test_explicit_real_post_is_consent_without_legacy_checkbox():
    handler, request, _, submit, seen, form = endpoint_case(consent=False)
    form.pop("confirm_real")
    assert await handler(request) == "rendered"
    submit.assert_awaited_once()
    submit.reject.assert_not_awaited()
    assert seen == ["configure", ("tenant", 3), ("actor", 31)]
