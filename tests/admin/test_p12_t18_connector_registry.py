"""No-DB checks for the connector registry's authorization and projections."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.exceptions import HTTPException

from app.admin import connector_registry as module


class Templates:
    async def TemplateResponse(self, request, template, context):
        self.template, self.context = template, context
        return SimpleNamespace(headers={})


def view():
    return SimpleNamespace(
        is_accessible=lambda request: module.ConnectorRegistryAdmin.is_accessible(None, request),
        templates=Templates(),
        name="Личные подключения",
    )


def request(actor, selected=4):
    return SimpleNamespace(state=SimpleNamespace(admin_user=actor), session={"app_tenant_id": selected})


def actor(**kwargs):
    return SimpleNamespace(id=7, is_active=True, has_admin_access=lambda: True, **kwargs)


@pytest.mark.asyncio
async def test_registry_uses_self_scopes_and_projects_no_private_material(monkeypatch):
    scopes = []

    @contextmanager
    def scope(tenant_id=None):
        scopes.append(tenant_id)
        yield

    monkeypatch.setattr(module, "tenant_scope", scope)
    monkeypatch.setattr(module, "current_tenant_id", lambda: None)
    monkeypatch.setattr(module, "is_bypass", lambda: True)
    accounts = AsyncMock(return_value=[{"authorization_id": 3, "state": "active", "subject_key": "PRIVATE"}])
    connections = AsyncMock(
        return_value=(
            [{"id": 5, "label": "Почта", "provider_label": "Почта", "state": "verified", "account_login": "PRIVATE"}],
            False,
        )
    )
    grants = AsyncMock(return_value=[{"grant_id": 6, "authorization_id": 3, "is_active": True}])
    monkeypatch.setattr(module.connector_authorizations, "list_personal", accounts)
    monkeypatch.setattr(module.connector_authorizations, "list_grants", grants)
    monkeypatch.setattr(module.workspace_connections, "list_for_actor", connections)
    v = view()
    response = await module.ConnectorRegistryAdmin.personal_connectors(v, request(actor()))
    accounts.assert_awaited_once_with(actor_id=7)
    connections.assert_awaited_once_with(tenant_id=4, actor_id=7)
    grants.assert_awaited_once_with(tenant_id=4, actor_id=7)
    assert scopes == [None, 4, 4]
    assert "PRIVATE" not in repr(v.templates.context)
    assert v.templates.context["accounts"] == [{"id": 3, "service": "Телеграм", "status": "Активна"}]
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_stale_workspace_never_shows_rows(monkeypatch):
    monkeypatch.setattr(module, "current_tenant_id", lambda: 4)
    monkeypatch.setattr(module, "is_bypass", lambda: False)
    monkeypatch.setattr(module.connector_authorizations, "list_personal", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        module.workspace_connections, "list_for_actor", AsyncMock(side_effect=module.ConnectionDenied())
    )
    grants = AsyncMock()
    monkeypatch.setattr(module.connector_authorizations, "list_grants", grants)
    v = view()
    await module.ConnectorRegistryAdmin.personal_connectors(v, request(actor()))
    assert v.templates.context["workspace_available"] is False
    assert not v.templates.context["connections"]
    grants.assert_not_awaited()


@pytest.mark.asyncio
async def test_anonymous_actor_is_denied_before_read(monkeypatch):
    accounts = AsyncMock()
    monkeypatch.setattr(module.connector_authorizations, "list_personal", accounts)
    with pytest.raises(HTTPException) as exc:
        await module.ConnectorRegistryAdmin.personal_connectors(view(), request(None))
    assert exc.value.status_code == 403
    accounts.assert_not_awaited()


def test_personal_registry_remains_reachable_without_a_workspace():
    from app.admin.scope import AdminScopeMiddleware

    assert not AdminScopeMiddleware._reads_workspace_data("/admin/personal-connectors")
    assert AdminScopeMiddleware._reads_workspace_data("/admin/personal-connectors/unknown")


@pytest.mark.asyncio
async def test_real_admin_route_renders_escaped_compact_metadata(monkeypatch):
    from pathlib import Path

    import httpx
    from sqladmin import Admin
    from sqladmin.authentication import AuthenticationBackend
    from sqlalchemy.orm import sessionmaker
    from starlette.applications import Starlette
    from starlette.staticfiles import StaticFiles

    operator = actor()
    operator.username = "operator"
    operator.role = SimpleNamespace(label="Оператор")
    operator.is_superuser = False

    class Auth(AuthenticationBackend):
        async def authenticate(self, request):
            request.state.admin_user = operator
            return True

    monkeypatch.setattr(module, "current_tenant_id", lambda: 4)
    monkeypatch.setattr(module, "is_bypass", lambda: False)
    monkeypatch.setattr(module.connector_authorizations, "list_personal", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.connector_authorizations, "list_grants", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        module.workspace_connections,
        "list_for_actor",
        AsyncMock(
            return_value=(
                [
                    {
                        "id": 5,
                        "label": "<script>BAD</script>",
                        "provider_label": "Почта",
                        "state": "verified",
                        "account_login": "NEVER-RENDER",
                    }
                ],
                False,
            )
        ),
    )
    app = Starlette()
    root = Path(__file__).resolve().parents[2]
    app.mount("/static", StaticFiles(directory=str(root / "app/static")), name="static")
    admin = Admin(
        app,
        session_maker=sessionmaker(),
        authentication_backend=Auth("synthetic-test-key"),
        templates_dir=str(root / "app/templates"),
    )
    admin.templates.env.globals.update(csrf_token=lambda: "synthetic", settings=SimpleNamespace(), debug=False)
    admin.add_view(module.ConnectorRegistryAdmin)
    admin.admin.mount("", app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid") as client:
        response = await client.get("/admin/personal-connectors")
    assert response.status_code == 200
    assert "&lt;script&gt;BAD&lt;/script&gt;" in response.text
    assert "<script>BAD</script>" not in response.text
    assert "NEVER-RENDER" not in response.text
    assert "Проверено" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_source_owner_is_read_only_and_list_is_compact():
    from app.admin.view_modules.sources import SourceAdmin

    assert SourceAdmin.column_labels["owner_user_id"] == "Пользователь для сбора"
    assert "owner_user_id" not in SourceAdmin.form_columns
    assert "date_from" not in SourceAdmin.column_list
    assert "date_to" not in SourceAdmin.column_list
