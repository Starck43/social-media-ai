"""P2-T15 static policy inspection in workspace settings and SQLAdmin."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from app.admin.view_modules.users import TenantAdmin
from app.models.tenancy.tenant import Tenant
from app.web import settings

__source_only__ = True


def _tenant():
    return Tenant(
        id=7,
        name="Synthetic workspace",
        slug="synthetic-workspace",
        plan="pro",
        plan_expires_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
        timezone="Europe/Moscow",
        is_active=True,
    )


def _request(tenant, *, manage):
    perms = NS(can_manage_workspace=lambda tenant_id: manage and tenant_id == tenant.id)
    state = NS(
        tenant_id=tenant.id,
        tenant=tenant,
        web_user=NS(id=3, is_active=True),
        web_perms=perms,
        memberships=[],
    )
    return NS(state=state, query_params={"tab": "workspace"}, session={})


async def test_manager_settings_context_contains_static_policy_without_live_quota_claim(monkeypatch):
    tenant = _tenant()
    request = _request(tenant, manage=True)
    captured = {}
    response = NS(headers={})
    monkeypatch.setattr(settings, "_visible_tabs", lambda *_args: ("workspace",))
    monkeypatch.setattr(settings, "_visible_tab", lambda *_args: "workspace")
    monkeypatch.setattr(settings, "_workspace_access", lambda *_args: True)
    monkeypatch.setattr(settings, "_plan_usage", AsyncMock(return_value={}))
    monkeypatch.setattr(
        settings,
        "render",
        lambda _request, template, **context: captured.update(template=template, context=context) or response,
    )

    rendered = await settings.settings_page(request)

    assert rendered is response
    assert captured["template"] == "web/settings.html"
    assert captured["context"]["entitlement_policy"] == tenant.entitlement_policy
    assert '"catalog_version": 1' in captured["context"]["entitlement_policy"]
    assert '"daily_quotas": {}' in captured["context"]["entitlement_policy"]
    assert "allowed" not in captured["context"]["entitlement_policy"]
    assert response.headers["Cache-Control"] == "no-store"


async def test_member_settings_context_does_not_expose_manager_policy(monkeypatch):
    tenant = _tenant()
    request = _request(tenant, manage=False)
    captured = {}
    monkeypatch.setattr(settings, "_visible_tabs", lambda *_args: ("workspace",))
    monkeypatch.setattr(settings, "_visible_tab", lambda *_args: "workspace")
    monkeypatch.setattr(settings, "_workspace_access", lambda *_args: True)
    monkeypatch.setattr(
        settings,
        "render",
        lambda _request, _template, **context: captured.update(context=context) or NS(headers={}),
    )

    await settings.settings_page(request)

    assert "entitlement_policy" not in captured["context"]
    assert captured["context"]["workspace_summary"] == {
        "name": "Synthetic workspace",
        "timezone": "Europe/Moscow",
    }


def test_plan_comparison_and_admin_details_expose_catalog_and_expiry():
    capability_row = next(row for row in settings._plan_table() if row["key"] == "product_capabilities")

    assert capability_row["label"] == "Product capabilities (catalog v1)"
    assert {capability_row[plan] for plan in Tenant.PLANS} == {"*"}
    assert "plan_expires_at" in TenantAdmin.column_details_list
    assert "entitlement_policy" in TenantAdmin.column_details_list
    assert TenantAdmin.column_labels["entitlement_policy"] == "Product capabilities (static policy)"
