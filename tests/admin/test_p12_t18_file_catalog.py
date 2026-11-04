"""No-database tests for the private file catalog admin view."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.exceptions import HTTPException

from app.admin import file_catalog as module
from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope


class Templates:
    async def TemplateResponse(self, request, template, context):
        self.context = context
        return SimpleNamespace(headers={})


def operator():
    return SimpleNamespace(id=7, is_active=True, has_admin_access=lambda: True, has_perm_for=lambda *args: True)


def request(params=None, selected=4, user=None):
    return SimpleNamespace(
        state=SimpleNamespace(admin_user=user or operator()),
        session={"app_tenant_id": selected},
        query_params=params or {},
    )


def view():
    return SimpleNamespace(
        is_accessible=lambda request: module.FileCatalogAdmin.is_accessible(None, request),
        templates=Templates(),
        name="Каталог файлов",
    )


@pytest.mark.asyncio
async def test_owner_uses_fresh_scoped_authorities_and_reprojects(monkeypatch):
    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", True)
    seen = []

    async def mounts(**kwargs):
        seen.append((current_tenant_id(), is_bypass(), kwargs))
        return [{"id": 5, "label": "Диск", "root": "PRIVATE"}]

    monkeypatch.setattr(module.catalog, "connections", mounts)
    listing = AsyncMock(
        return_value={
            "items": [{"id": 3, "name": "Файл", "kind": "file", "observed_at": "сейчас", "path": "PRIVATE"}],
            "next_after": 3,
        }
    )
    monkeypatch.setattr(module.catalog, "list_page", listing)
    monkeypatch.setattr(module, "catalog_scan_status", AsyncMock(return_value={"state": "partial"}))
    v = view()
    with tenant_scope(bypass=True):
        response = await module.FileCatalogAdmin.file_catalog(v, request())
    assert seen == [(4, False, {"tenant_id": 4, "actor_id": 7})]
    listing.assert_awaited_once_with(tenant_id=4, actor_id=7, connection_id=5, query="", after=0, limit=50)
    assert "PRIVATE" not in repr(v.templates.context)
    assert v.templates.context["next_url"] == "?connection_id=5&q=&after=3"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_foreign_connection_and_revoked_detail_are_unavailable(monkeypatch):
    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", True)
    monkeypatch.setattr(module.catalog, "connections", AsyncMock(return_value=[{"id": 5, "label": "Диск"}]))
    detail = AsyncMock(side_effect=module.ConnectionDenied())
    monkeypatch.setattr(module.catalog, "detail", detail)
    with tenant_scope(4):
        with pytest.raises(HTTPException) as exc:
            await module.FileCatalogAdmin.file_catalog(view(), request({"connection_id": "6", "object_id": "1"}))
        assert exc.value.status_code == 404
        detail.assert_not_awaited()
        with pytest.raises(HTTPException) as exc:
            await module.FileCatalogAdmin.file_catalog(view(), request({"connection_id": "5", "object_id": "1"}))
        assert exc.value.status_code == 404
    detail.assert_awaited_once_with(tenant_id=4, actor_id=7, connection_id=5, object_id=1)


@pytest.mark.asyncio
async def test_feature_flag_and_unselected_owner_fail_before_reads(monkeypatch):
    reads = AsyncMock()
    monkeypatch.setattr(module.catalog, "connections", reads)
    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", False)
    with tenant_scope(4), pytest.raises(HTTPException) as exc:
        await module.FileCatalogAdmin.file_catalog(view(), request())
    assert exc.value.status_code == 403
    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", True)
    with tenant_scope(bypass=True), pytest.raises(HTTPException) as exc:
        await module.FileCatalogAdmin.file_catalog(view(), request(selected=None))
    assert exc.value.status_code == 404
    reads.assert_not_awaited()


@pytest.mark.asyncio
async def test_detail_projects_history_without_private_fields(monkeypatch):
    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", True)
    monkeypatch.setattr(module.catalog, "connections", AsyncMock(return_value=[{"id": 5, "label": "Диск"}]))
    monkeypatch.setattr(
        module.catalog,
        "detail",
        AsyncMock(
            return_value={
                "name": "Файл",
                "kind": "file",
                "path": "PRIVATE",
                "observations": [
                    {"number": 1, "name": "Файл", "kind": "file", "observed_at": "сейчас", "secret": "PRIVATE"}
                ],
            }
        ),
    )
    monkeypatch.setattr(module, "catalog_scan_status", AsyncMock(return_value=None))
    v = view()
    with tenant_scope(4):
        await module.FileCatalogAdmin.file_catalog(v, request({"connection_id": "5", "object_id": "8"}))
    assert "PRIVATE" not in repr(v.templates.context)
    assert v.templates.context["detail"]["observations"][0]["number"] == 1


@pytest.mark.parametrize("value", ["-1", "1.0", "x", "2147483648", "１２"])
def test_bad_numbers_are_rejected(value):
    with pytest.raises(HTTPException) as exc:
        module._number(value)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_sqladmin_route_escapes_names_and_leaks_no_paths(monkeypatch):
    from pathlib import Path

    import httpx
    from sqladmin import Admin
    from sqladmin.authentication import AuthenticationBackend
    from sqlalchemy.orm import sessionmaker
    from starlette.applications import Starlette
    from starlette.staticfiles import StaticFiles

    user = operator()
    user.username = "operator"
    user.role = SimpleNamespace(label="Оператор")
    user.is_superuser = False

    class Auth(AuthenticationBackend):
        async def authenticate(self, request):
            request.state.admin_user = user
            return True

    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", True)
    monkeypatch.setattr(
        module.catalog, "connections", AsyncMock(return_value=[{"id": 5, "label": "Диск", "root": "SECRET-ROOT"}])
    )
    monkeypatch.setattr(
        module.catalog,
        "list_page",
        AsyncMock(
            return_value={
                "items": [
                    {
                        "id": 3,
                        "name": "<script>BAD</script>",
                        "kind": "file",
                        "observed_at": "сейчас",
                        "path": "SECRET-PATH",
                    }
                ],
                "next_after": None,
            }
        ),
    )
    monkeypatch.setattr(module, "catalog_scan_status", AsyncMock(return_value=None))
    app = Starlette()
    root = Path(__file__).resolve().parents[2]
    app.mount("/static", StaticFiles(directory=str(root / "app/static")), name="static")
    admin = Admin(
        app,
        session_maker=sessionmaker(),
        authentication_backend=Auth("synthetic-key"),
        templates_dir=str(root / "app/templates"),
    )
    admin.templates.env.globals.update(csrf_token=lambda: "synthetic", settings=SimpleNamespace(), debug=False)
    admin.add_view(module.FileCatalogAdmin)
    admin.admin.mount("", app)
    with tenant_scope(4):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid") as client:
            response = await client.get("/admin/file-catalog")
    assert response.status_code == 200
    assert "&lt;script&gt;BAD&lt;/script&gt;" in response.text
    assert "<script>BAD</script>" not in response.text
    assert "SECRET-ROOT" not in response.text and "SECRET-PATH" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_menu_hides_file_catalog_when_disabled_or_without_platform_source_view(monkeypatch):
    v = view()
    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", False)
    assert not v.is_accessible(request())
    monkeypatch.setattr(module.settings, "FILE_CATALOG_UI_ENABLED", True)
    denied = operator()
    denied.has_perm_for = lambda *args: False
    assert not v.is_accessible(request(user=denied))
