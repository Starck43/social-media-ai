"""No-DB tests of scoped evidence admin lists."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.exceptions import HTTPException

from app.admin import knowledge_registry as module
from app.core.permissions import get_current_user, permission_scope
from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope


class Templates:
    async def TemplateResponse(self, request, template, context):
        self.context = context
        return SimpleNamespace(headers={})


def operator():
    return SimpleNamespace(id=7, is_active=True, has_admin_access=lambda: True, has_perm_for=lambda *args: True)


def request(params=None, selected=4):
    return SimpleNamespace(
        state=SimpleNamespace(admin_user=operator()), session={"app_tenant_id": selected}, query_params=params or {}
    )


def view():
    return SimpleNamespace(
        is_accessible=lambda req: module.KnowledgeRegistryAdmin.is_accessible(None, req),
        _read=module.KnowledgeRegistryAdmin._read,
        templates=Templates(),
        name="Материалы и версии",
    )


class Query:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def filter(self, condition):
        self.calls.append("filter")
        return self

    def order_by(self, column):
        self.calls.append("order")
        return self

    def limit(self, size):
        self.calls.append(("limit", size))
        return self

    def __await__(self):
        async def result():
            return self.records

        return result().__await__()


@pytest.mark.asyncio
async def test_owner_must_be_member_and_reads_with_membership_scope(monkeypatch):
    grants = frozenset({("source", module.ActionType.VIEW)})
    membership = AsyncMock(return_value=[(4, grants)])
    monkeypatch.setattr(module.AdminScopeMiddleware, "_memberships", membership)
    monkeypatch.setattr(module, "has_permission", lambda actor, model, action: True)
    query = Query(
        [SimpleNamespace(id=1, title="Запись", context_type="knowledge", data_boundary="cloud", excerpt="PRIVATE")]
    )
    seen = []

    def visible(boundary):
        seen.append((boundary, current_tenant_id(), is_bypass(), get_current_user().id))
        return query

    monkeypatch.setattr(module.KnowledgeSnippet.objects, "visible", visible)
    v = view()
    with tenant_scope(bypass=True):
        response = await module.KnowledgeRegistryAdmin.knowledge_registry(v, request())
    assert seen == [("cloud", 4, False, 7)]
    assert query.calls == ["filter", "order", ("limit", 51)]
    assert "PRIVATE" not in repr(v.templates.context)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_stale_or_foreign_workspace_denied_before_query(monkeypatch):
    monkeypatch.setattr(module.AdminScopeMiddleware, "_memberships", AsyncMock(return_value=[(8, frozenset())]))
    read = AsyncMock()
    v = view()
    v._read = read
    with tenant_scope(bypass=True), pytest.raises(HTTPException) as exc:
        await module.KnowledgeRegistryAdmin.knowledge_registry(v, request())
    assert exc.value.status_code == 403
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_workspace_permission_denied_before_query(monkeypatch):
    monkeypatch.setattr(module, "has_permission", lambda *args: False)
    visible = AsyncMock()
    monkeypatch.setattr(module.KnowledgeSnippet.objects, "visible", visible)
    req = request()
    with tenant_scope(4), permission_scope(req.state.admin_user), pytest.raises(HTTPException) as exc:
        await module.KnowledgeRegistryAdmin.knowledge_registry(view(), req)
    assert exc.value.status_code == 403
    visible.assert_not_called()


@pytest.mark.asyncio
async def test_revision_list_calls_visible_and_does_not_expose_content(monkeypatch):
    monkeypatch.setattr(module, "has_permission", lambda *args: True)
    record = SimpleNamespace(id=2, document_id=3, version=1, status="approved", body_text="PRIVATE")
    query = Query([record])
    visible = lambda boundary: query
    monkeypatch.setattr(module.DocumentRevision.objects, "visible", visible)
    v = view()
    req = request({"kind": "revisions", "boundary": "company"})
    with tenant_scope(4), permission_scope(req.state.admin_user):
        await module.KnowledgeRegistryAdmin.knowledge_registry(v, req)
    assert v.templates.context["rows"] == [{"id": 2, "document_id": 3, "version": 1, "status": "Согласовано"}]
    assert "PRIVATE" not in repr(v.templates.context)


@pytest.mark.asyncio
async def test_invalid_boundary_is_rejected(monkeypatch):
    read = AsyncMock()
    v = view()
    v._read = read
    with tenant_scope(4), pytest.raises(HTTPException) as exc:
        await module.KnowledgeRegistryAdmin.knowledge_registry(v, request({"boundary": "wrong"}))
    assert exc.value.status_code == 400
    read.assert_not_awaited()


def test_evidence_managers_embed_acl_boundary_and_tenant_constraints():
    from sqlalchemy.dialects import postgresql

    from app.core.access_catalog import workspace_grants

    user = operator()
    with tenant_scope(4), permission_scope(user, member_grants=workspace_grants("viewer")):
        for model in (module.KnowledgeSnippet, module.KnowledgeDocument, module.DocumentRevision):
            query = model.objects.visible("cloud")
            sql = " ".join(str(c.compile(dialect=postgresql.dialect())) for c in query._criterion)
            assert "acl_scope" in sql and "data_boundary" in sql
            assert "tenant_id" in sql


@pytest.mark.asyncio
async def test_sqladmin_renders_escaped_metadata(monkeypatch):
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

    monkeypatch.setattr(module, "has_permission", lambda *args: True)
    row = SimpleNamespace(
        id=1, title="<script>BAD</script>", context_type="knowledge", data_boundary="cloud", excerpt="SECRET"
    )
    monkeypatch.setattr(module.KnowledgeSnippet.objects, "visible", lambda boundary: Query([row]))
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
    admin.add_view(module.KnowledgeRegistryAdmin)
    admin.admin.mount("", app)
    with tenant_scope(4), permission_scope(user):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid") as client:
            response = await client.get("/admin/knowledge-registry")
    assert response.status_code == 200
    assert "&lt;script&gt;BAD&lt;/script&gt;" in response.text
    assert "<script>BAD</script>" not in response.text and "SECRET" not in response.text
    assert response.headers["cache-control"] == "no-store"
