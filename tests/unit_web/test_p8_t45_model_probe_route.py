"""Owner-scoped settings model test route contracts with external services doubled."""

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
_MISSING = object()


def route_module(*, model=_MISSING, csrf=True, local_result=None, real_result=None):
    if model is _MISSING:
        model = NS(id=7, owner_user_id=2, name="Fixture", provider=NS(is_active=True))
    manager = NS(get=AsyncMock(return_value=model))
    manager.select_related = Mock(return_value=manager)
    models = NS(LLMModel=NS(objects=manager))
    catalog = NS(require_actor=Mock())
    probe = NS(
        probe_short=Mock(return_value=local_result or {"ok": True, "tier": "mock", "text": "local"}),
        probe_real_capped=AsyncMock(return_value=real_result or {"ok": True, "tier": "real", "text": "real"}),
        provider_limit_hint=Mock(return_value={"code": "quota", "message": "Safe quota hint"}),
    )
    tenants = NS(get=AsyncMock(return_value=NS(id=5)))
    flashes = []
    deps = NS(
        add_flash=lambda request, level, message: flashes.append((level, message)),
        ensure_csrf=lambda request, token: csrf and token == "valid",
    )
    module = load_isolated_source(
        "app.web._p8_t45_settings_llm",
        ROOT / "app/web/settings_llm.py",
        {
            "app.web.deps": deps,
            "app.models": models,
            "app.services.ai.personal_catalog": catalog,
            "app.services.ai.model_probe": probe,
            "app.models.managers.tenant_manager": NS(tenants=tenants),
        },
    )
    return module, model, manager, probe, tenants, catalog, flashes


def request(*, tenant_id=5, user_id=2, token="valid", session=None):
    return NS(
        state=NS(web_user=NS(id=user_id, is_active=True), tenant_id=tenant_id),
        session=session if session is not None else {},
        form=AsyncMock(return_value={"_csrf": token}),
    )


@pytest.mark.asyncio
async def test_csrf_failure_stops_before_catalog_lookup_or_provider_dispatch():
    module, _, manager, probe, _, catalog, _ = route_module(csrf=False)
    response = await module._run_probe(request(token="bad"), model_id="7", mode="real", tenant_id=5, consent="yes")
    assert response.status_code == 403
    manager.get.assert_not_awaited()
    catalog.require_actor.assert_not_called()
    probe.probe_real_capped.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_probe_requires_explicit_consent_before_catalog_lookup():
    module, _, manager, probe, _, _, flashes = route_module()
    response = await module._run_probe(request(), model_id="7", mode="real", tenant_id=5, consent="")
    assert response.status_code == 303
    manager.get.assert_not_awaited()
    probe.probe_real_capped.assert_not_awaited()
    assert flashes


@pytest.mark.asyncio
async def test_local_probe_is_owner_scoped_and_never_dispatches_real_provider():
    module, model, manager, probe, _, catalog, _ = route_module()
    session = {}
    response = await module._run_probe(request(session=session), model_id="7", mode="local", tenant_id=None, consent="")
    assert response.status_code == 303 and response.headers["location"].endswith("?tab=models")
    manager.get.assert_awaited_once_with(id=7, owner_user_id=2)
    catalog.require_actor.assert_called_once_with(2)
    probe.probe_short.assert_called_once_with(model)
    probe.probe_real_capped.assert_not_awaited()
    assert session["settings_model_probe_result"]["mode"] == "local"


@pytest.mark.asyncio
async def test_real_probe_uses_model_test_billing_purpose_and_compact_safe_session():
    module, model, _, probe, tenants, _, _ = route_module(
        real_result={
            "ok": False,
            "tier": "real",
            "text": "quota exhausted",
            "response_json": {"private": "fixture-secret"},
            "limit_hint": {"code": "quota", "message": "Safe quota hint"},
        }
    )
    session = {}
    response = await module._run_probe(request(session=session), model_id="7", mode="real", tenant_id=5, consent="yes")
    assert response.status_code == 303
    tenants.get.assert_awaited_once_with(id=5, is_active=True)
    probe.probe_real_capped.assert_awaited_once_with(model, purpose="model_test")
    assert session["settings_model_probe_result"]["limit_hint"] == {"code": "quota", "message": "Safe quota hint"}
    assert "response_json" not in repr(session)
    assert "fixture-secret" not in repr(session)


@pytest.mark.asyncio
async def test_real_probe_rejects_stale_or_missing_active_workspace():
    module, _, manager, probe, _, _, flashes = route_module()
    response = await module._run_probe(request(tenant_id=5), model_id="7", mode="real", tenant_id=6, consent="yes")
    assert response.status_code == 303
    manager.get.assert_awaited_once_with(id=7, owner_user_id=2)
    probe.probe_real_capped.assert_not_awaited()
    assert flashes


@pytest.mark.asyncio
async def test_foreign_or_malformed_model_id_does_not_reach_probe():
    module, _, manager, probe, _, _, _ = route_module(model=None)
    response = await module._run_probe(request(), model_id="7", mode="local", tenant_id=None, consent="")
    assert response.status_code == 303
    manager.get.assert_awaited_once_with(id=7, owner_user_id=2)
    probe.probe_short.assert_not_called()
    manager.get.reset_mock()
    probe.probe_short.reset_mock()
    response = await module._run_probe(request(), model_id="7,8", mode="local", tenant_id=None, consent="")
    assert response.status_code == 303
    manager.get.assert_not_awaited()
    probe.probe_real_capped.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_context_exposes_aggregate_counts_and_latest_failure_only_to_owner():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    models = [
        NS(id=7, use_count=12, fail_count=3, last_error_at=now, last_success_at=now - timedelta(seconds=1)),
        NS(id=8, use_count=4, fail_count=0, last_error_at=now, last_success_at=now),
    ]
    catalog = {
        "providers": [
            {
                "id": 4,
                "name": "Fixture",
                "uid": "fixture",
                "base_url": "https://fixture.invalid",
                "has_api_key": True,
                "models": [{"id": 7}, {"id": 8}],
            }
        ],
        "models": [{"id": 7}, {"id": 8}],
        "effective_default_model_id": 7,
    }
    manager = NS(filter=AsyncMock(return_value=models))
    personal_catalog = NS(catalog_metadata=AsyncMock(return_value=catalog))
    llm_models = NS(LLMModelManager=Mock(return_value=manager))
    module = load_isolated_source(
        "app.web._p8_t45_settings_context",
        ROOT / "app/web/settings_llm.py",
        {
            "app.web.deps": NS(add_flash=Mock(), ensure_csrf=Mock()),
            "app.services.ai.personal_catalog": personal_catalog,
            "app.models.managers.llm_model_manager": llm_models,
        },
    )
    req = request(user_id=2, session={"settings_model_probe_result": {"owner_user_id": 3, "model_id": 7}})
    req.state.tenant = NS(agent_model_id=7)
    context = await module.settings_llm_context(req)
    manager.filter.assert_awaited_once_with(owner_user_id=2)
    personal_catalog.catalog_metadata.assert_awaited_once_with(user_id=2)
    assert context["llm_catalog"]["models"] == [
        {"id": 7, "use_count": 12, "fail_count": 3, "success_count": 9, "has_recent_error": True},
        {"id": 8, "use_count": 4, "fail_count": 0, "success_count": 4, "has_recent_error": False},
    ]
    assert context["llm_catalog"]["providers"][0]["models"] == context["llm_catalog"]["models"]
    assert context["model_probe_result"] is None
