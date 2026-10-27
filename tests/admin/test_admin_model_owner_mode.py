"""Single-owner requests use actual client code over offline HTTP; no DB imports."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_admin_model_billing_integration import endpoint_case
from test_admin_model_diagnostics import PAYLOAD, stack
from test_admin_model_probe import ROOT, action_module, model, probe_module, template_env

import app.services.ai.personal_dispatch as personal_dispatch


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,limit", [("", 8), ("Что такое API?", 512)])
async def test_actual_client_owner_mode_sends_once_and_retains_json_and_usage(prompt, limit, monkeypatch):
    probe, dependency, selected, sent, client = stack()
    dependency.__isolated_imports__["app.core.config"].settings.PERSONAL_BILLING_ENABLED = False
    # P3 made the admission path revalidate the catalog owner before the flag
    # check; the isolated stack owns no DB, so that check is doubled here.
    owner_check = AsyncMock(return_value=None)
    monkeypatch.setattr(personal_dispatch, "validate_personal_dispatch", owner_check)
    # Exercise the real flag-off admission branch, not an unconditional fake.
    dependency._personal_admission = dependency._actual_personal_admission
    result = await probe.probe_real_capped(selected, prompt)
    owner_check.assert_awaited_once()  # The real admission branch ran the owner check.
    assert result["ok"] and result["response_json"] == PAYLOAD and result["tokens_spent"] == 5
    assert result["accounting_status"] == "standard" and result["http_status"] == 200
    assert result["max_tokens"] == limit and len(sent) == 1
    dependency._record_llm_usage.assert_awaited_once()
    dependency.journal_chat_attempt.assert_awaited_once()
    assert dependency._observe_personal_outcome.await_args.args[0] is None
    assert client._probe_diagnostics["effective_max_tokens"] == limit


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["", "bad", "9999999999999999999999"])
async def test_owner_endpoint_ignores_submitted_payer_and_workspace_but_binds_actor(tenant):
    handler, request, lookup, submit, seen, form = endpoint_case(enabled=False, tenant=tenant)
    form["payer_user_id"] = "someone-else"
    assert await handler(request) == "rendered"
    assert seen == ["configure", ("actor", 31)]
    submit.assert_awaited_once_with("view", request, "7", "real", "UNTRUSTED_PROMPT")
    lookup.assert_not_awaited()
    submit.reject.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("changes,status", [({"allowed": False}, 403), ({"csrf": False}, 403), ({"admin": False}, 403)])
async def test_both_modes_keep_authority_and_csrf_guards(enabled, changes, status):
    from fastapi import HTTPException

    handler, request, _, submit, _, _ = endpoint_case(enabled=enabled, **changes)
    if changes.get("admin") is False:
        assert (await handler(request)).status_code == status
    else:
        with pytest.raises(HTTPException) as error:
            await handler(request)
        assert error.value.status_code == status
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_render_has_no_billing_warning_or_workspace_setup():
    probe, _ = probe_module()
    actions, _, view = action_module(probe)
    context = await actions.LLMModelActions._render(view, NS(state=NS(admin_user=NS(is_superuser=True, is_platform_admin=True))), model())
    assert not context["billing_ready"] and not context["billing_tenants"]
    assert context["billing_block_reason"] == ""
    env = template_env()
    template = env.from_string(
        (ROOT / "app/templates/llm_model/test_results.html")
        .read_text()
        .replace('{% extends "sqladmin/layout.html" %}', "")
    )
    html = template.render(**context)
    assert "PERSONAL_BILLING_ENABLED" not in html and 'name="tenant_id"' not in html
    assert "Платный запрос требует" not in html and 'value="real"' in html
