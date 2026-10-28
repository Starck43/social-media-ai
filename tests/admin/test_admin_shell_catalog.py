"""Actual SQLAdmin routes/forms with explicit identity/storage doubles; no DB."""

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from source_import_isolation import load_isolated_source
from sqladmin import Admin
from sqladmin.authentication import AuthenticationBackend
from sqladmin.authorization import Action
from sqladmin.pagination import Pagination
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.datastructures import FormData
from starlette.staticfiles import StaticFiles
from test_admin_model_permission_policy import CATALOG, TYPES, user_with
from wtforms import validators

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def source():
    config = NS(settings=NS(DB_SCHEMA="synthetic_no_database"))
    base = load_isolated_source("app.models._catalog_base", ROOT / "app/models/base.py", {"app.core.config": config})
    deps = {"app.core.config": config, "app.models.base": base, "app.types": TYPES, "app.core.access_catalog": CATALOG}
    role = load_isolated_source("app.models.identity._catalog_role", ROOT / "app/models/identity/role.py", deps)
    permission = load_isolated_source(
        "app.models.identity._catalog_permission", ROOT / "app/models/identity/permission.py", deps
    )
    model_type = load_isolated_source(
        "app.models.identity._catalog_model_type", ROOT / "app/models/identity/model_type.py", deps
    )
    users = load_isolated_source(
        "app.models.identity._catalog_user",
        ROOT / "app/models/identity/user.py",
        {**deps, "app.core.decorators": NS(app_label=lambda _: lambda cls: cls)},
    )
    models = NS(PlatformRole=role.PlatformRole, User=users.User, Permission=permission.Permission)
    formatting = NS(
        **{
            name: (lambda *args: "")
            for name in ["bool_formatter", "date_formatter", "datetime_formatter", "empty_formatter"]
        }
    )
    admin_base = load_isolated_source(
        "app.admin._catalog_base", ROOT / "app/admin/base.py", {"app.types": TYPES, "app.admin.formatters": formatting}
    )
    csrf = load_isolated_source("_catalog_csrf", ROOT / "app/admin/csrf.py")
    # Real context manager, with no tenant or transport initialization.
    contexts = load_isolated_source(
        "_catalog_contexts",
        ROOT / "app/core/permissions.py",
        {
            "app.types": TYPES,
            "app.core.access_catalog": CATALOG,
            "app.core.tenant_context": NS(current_tenant_id=lambda: None, is_bypass=lambda: False),
        },
    )
    catalog = load_isolated_source(
        "_catalog_editor",
        ROOT / "app/admin/catalog.py",
        {
            "app.admin.base": admin_base,
            "app.admin.csrf": csrf,
            "app.core.permissions": contexts,
            "app.models": models,
        },
    )
    backend = load_isolated_source(
        "_catalog_authorization", ROOT / "app/admin/authorization.py", {"app.models": models, "app.types": TYPES}
    )
    tree = ast.parse((ROOT / "app/admin/view_modules/users.py").read_text())
    nodes = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in {"RoleAdmin", "PermissionAdmin"}
    ]
    ns = dict(
        BaseAdmin=admin_base.BaseAdmin,
        PlatformRoleGrantAdmin=catalog.PlatformRoleGrantAdmin,
        PermissionLabelAdmin=catalog.PermissionLabelAdmin,
        PlatformRole=role.PlatformRole,
        Permission=permission.Permission,
        validators=validators,
        FilteredSelectMultipleField=load_isolated_source(
            "_catalog_filtered_select", ROOT / "app/admin/filtered_select.py"
        ).FilteredSelectMultipleField,
    )
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "actual-catalog-views", "exec"), ns
    )
    return NS(
        **ns,
        csrf=csrf,
        backend=backend,
        models=models,
        role_module=role,
        contexts=contexts,
        canonical_model_type=model_type.ModelType,
    )


def actor(role="admin", active=True):
    return user_with(role=role, active=active)


def console(source, user=None):
    user = user if user is not None else actor()
    user.username = "verified-admin"
    user.role.label = CATALOG.role_label(user.role.codename)
    source.Permission.objects = NS(prefetch_related=Mock(return_value=NS(all=AsyncMock(return_value=[]))))
    source.models.User.objects = NS(prefetch_related=Mock())
    source.models.User.objects.prefetch_related.return_value = NS(get=AsyncMock(return_value=user))
    csrf = source.csrf.CSRFTokenManager("synthetic-not-a-secret")

    class Auth(AuthenticationBackend):
        csrf_manager = csrf

        async def authenticate(self, request):
            request.state.admin_user = user
            return user.has_admin_access()

    app = Starlette()
    app.mount("/static", StaticFiles(directory=str(ROOT / "app/static")), name="static")
    backend = source.backend.AdminAuthorizationBackend()
    admin = Admin(
        app,
        session_maker=sessionmaker(),
        authentication_backend=Auth("synthetic-session-key"),
        authorization_backend=backend,
        templates_dir=str(ROOT / "app/templates"),
    )
    admin.templates.env.globals["csrf_token"] = csrf.generate_token
    admin.add_view(source.RoleAdmin)
    admin.add_view(source.PermissionAdmin)
    # Production identity and catalog persistence are the only replaced boundaries.
    role = source.PlatformRole(id=2, codename="operator", description="Operator", permissions=[])
    target = NS(
        get=AsyncMock(return_value=role),
        set_permissions=AsyncMock(return_value=1),
        get_with_permissions=AsyncMock(return_value=role),
    )
    source.PlatformRole.objects = target
    for view in admin._views:
        view.get_object_for_details = AsyncMock(
            return_value=role if view.identity == "platform-role" else source.Permission(id=1, name="Read")
        )
        view.get_object_for_edit = view.get_object_for_details
        session = Mock()
        session.execute.return_value.scalars.return_value.unique.return_value.all.return_value = []
        factory = Mock()
        factory.class_ = object
        factory.return_value.__enter__ = Mock(return_value=session)
        factory.return_value.__exit__ = Mock(return_value=False)
        view.session_maker = factory
    return NS(app=app, admin=admin, backend=backend, target=target, user=user, csrf=csrf)


@pytest.mark.asyncio
async def test_external_route_loads_same_menu_grants(source):
    ctx = console(source, actor("superuser"))
    request = NS(state=NS(admin_user=ctx.user), app=NS(state=NS(admin=ctx.admin)), session={})
    # Previously resolve_user alone left every menu item inaccessible.
    assert not ctx.backend.has_permission(request, "platform-role", Action.LIST)
    result = await source.backend.require_admin_perm(request, "llmmodel", TYPES.ActionType.CONFIGURE)
    assert result is ctx.user
    assert all(view.is_accessible(request) for view in ctx.admin._views)


@pytest.mark.asyncio
async def test_an_admin_console_is_bounded_by_its_grants(source):
    """An `admin` is bounded by the grants its role carries, model by model.

    The owner holds the wildcard; an admin that was granted one right on the
    model catalog sees the console entry for the models and not for others.
    Asserted on `model_permissions` — the single answer both the console and
    the API read — because this fixture resolves identity through the auth
    backend rather than a session token, so `get_grants` has nothing to load.
    """
    user = user_with(TYPES.ActionType.VIEW, role="admin", resource="llm.model")
    ctx = console(source, user)
    assert ctx.user.model_permissions("llmmodel") == {TYPES.ActionType.VIEW}
    assert ctx.user.model_permissions("llmprovider") == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "operator"])
async def test_menu_loading_never_elevates_ungranted_identity(source, role):
    ctx = console(source, actor(role))
    request = NS(state=NS(admin_user=ctx.user), app=NS(state=NS(admin=ctx.admin)), session={})
    with pytest.raises(HTTPException) as exc:
        await source.backend.require_admin_perm(request, "llmmodel", TYPES.ActionType.CONFIGURE)
    assert exc.value.status_code == 403
    assert not ctx.backend.has_permission(request, "llm-model", Action.EDIT)


@pytest.mark.asyncio
async def test_real_form_fields_are_narrow_and_clear_all_is_valid(source):
    ctx = console(source)
    role_view, permission_view = ctx.admin._views
    role_form = await role_view.scaffold_form()
    form = role_form(FormData())
    assert set(form.data) == {"permissions"}
    assert form.validate() and form.data["permissions"] == []
    assert set((await permission_view.scaffold_form())().data) == {"name"}
    assert not role_view.can_create and not role_view.can_delete
    assert not permission_view.can_create and not permission_view.can_delete
    assert "description" not in permission_view._list_prop_names  # not an ORM attribute


@pytest.mark.asyncio
async def test_role_route_shows_actor_and_guarded_form(source):
    ctx = console(source, actor("superuser"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        response = await client.get("/admin/platform-role/edit/2")
    assert response.status_code == 200, response.text
    assert "verified-admin" in response.text and "Собственник платформы" in response.text
    # The console names the level truthfully: the owner is not a plain admin.
    assert "Собственник: полный доступ ко всем данным" in response.text
    assert 'name="csrf_token"' in response.text and 'name="permissions"' in response.text
    assert 'name="users"' not in response.text and 'name="codename"' not in response.text
    assert "/admin/permission/list" in response.text


@pytest.mark.asyncio
async def test_only_the_platform_owner_edits_the_role_catalog(source):
    """Role grants are the owner's alone; a platform admin keeps its own rights."""
    owner = actor("superuser")
    ctx = console(source, owner)
    ctx.target.get.return_value.codename = "operator"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        response = await client.get("/admin/platform-role/edit/2")
        assert response.status_code == 200, response.text
        assert "Собственник: полный доступ ко всем данным" in response.text
        assert 'name="permissions"' in response.text


@pytest.mark.asyncio
async def test_platform_admin_gets_no_catalog_edit_access(source):
    """`admin` runs the platform; it does not rewrite the permission matrix."""
    ctx = console(source)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        for route in ["platform-role/edit/2", "permission/edit/1"]:
            assert (await client.get("/admin/" + route)).status_code == 403
            assert (await client.post("/admin/" + route, data={"name": "Bad"})).status_code == 403
    ctx.target.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "admin", "operator"])
async def test_owner_can_open_every_role_edit_route(source, role):
    ctx = console(source, actor("superuser"))
    ctx.target.get.return_value.codename = role
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        response = await client.get("/admin/platform-role/edit/2")
    assert response.status_code == 200
    assert 'name="permissions"' in response.text
    assert 'name="codename"' not in response.text
    ctx.target.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_role", ["user", "admin", "operator"])
async def test_operator_cannot_edit_either_catalog_even_with_broad_grants(source, target_role):
    user = actor("operator")
    user.model_permissions = lambda _: set(TYPES.ActionType)
    ctx = console(source, user)
    ctx.target.get.return_value.codename = target_role
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        for route in ["platform-role/edit/2", "permission/edit/1"]:
            assert (await client.get("/admin/" + route)).status_code == 403
            assert (await client.post("/admin/" + route, data={"name": "Bad"})).status_code == 403
    ctx.target.set_permissions.assert_not_awaited()


def request_for(ctx, token=None):
    return NS(
        state=NS(admin_user=ctx.user), form=AsyncMock(return_value={"csrf_token": token or ctx.csrf.generate_token()})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("target_role", ["user", "admin", "operator"])
async def test_role_writer_uses_canonical_manager_for_every_role_with_actor_scope(source, target_role):
    ctx = console(source, actor("superuser"))
    ctx.target.get.return_value.codename = target_role
    observed = []

    async def write(role_id, permissions):
        observed.append((source.contexts.get_current_user(), role_id, permissions))

    ctx.target.set_permissions.side_effect = write
    await ctx.admin._views[0].update_model(request_for(ctx), "2", {"permissions": ["1", "3"]})
    assert observed == [(ctx.user, 2, [1, 3])]
    assert ctx.target.get.return_value.codename == target_role
    assert source.contexts.get_current_user() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"users": [1]}, {"permissions": [], "codename": "admin"}, {"name": "New"}])
async def test_role_writer_rejects_extra_fields(source, data):
    ctx = console(source, actor("superuser"))
    with pytest.raises(HTTPException):
        await ctx.admin._views[0].update_model(request_for(ctx), "2", data)
    ctx.target.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("failure", ["csrf", "revoked", "inactive"])
async def test_catalog_write_checks_csrf_and_fresh_actor(source, which, failure):
    ctx = console(source, actor("superuser"))
    request = request_for(ctx, "invalid" if failure == "csrf" else None)
    if failure != "csrf":
        fresh = actor("operator" if failure == "revoked" else "admin", active=failure != "inactive")
        source.models.User.objects.prefetch_related.return_value.get.return_value = fresh
    with pytest.raises(HTTPException) as exc:
        await ctx.admin._views[which].update_model(
            request, "2", {"permissions": []} if which == 0 else {"name": "Label"}
        )
    assert exc.value.status_code == 403
    ctx.target.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"action_type": "DELETE"}, {"name": "x", "roles": [1]}, {"name": " "}])
async def test_permission_writer_rejects_identity_or_grant_changes(source, data):
    ctx = console(source, actor("superuser"))
    with pytest.raises(HTTPException) as exc:
        await ctx.admin._views[1].update_model(request_for(ctx), "1", data)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("target_role", ["user", "admin", "operator"])
async def test_http_role_post_clears_grants_and_rejects_missing_csrf_for_every_role(source, target_role):
    ctx = console(source, actor("superuser"))
    ctx.target.get.return_value.codename = target_role
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        response = await client.post("/admin/platform-role/edit/2", data={"save": "Save"})
        assert response.status_code == 400  # SQLAdmin catches the rejected CSRF write.
        ctx.target.set_permissions.assert_not_awaited()
        response = await client.post(
            "/admin/platform-role/edit/2", data={"save": "Save", "csrf_token": ctx.csrf.generate_token()}
        )
        assert response.status_code == 302, response.text
    ctx.target.set_permissions.assert_awaited_once_with(2, [])


@pytest.mark.asyncio
async def test_permission_label_writer_does_not_forward_other_fields(source, monkeypatch):
    ctx = console(source, actor("superuser"))
    writer = AsyncMock(return_value=NS(id=1, name="Readable"))
    monkeypatch.setattr(source.BaseAdmin, "update_model", writer)
    result = await ctx.admin._views[1].update_model(request_for(ctx), "1", {"name": " Readable "})
    assert result.name == "Readable"
    assert writer.await_args.args[-1] == {"name": "Readable"}


@pytest.mark.asyncio
async def test_escaped_verified_identity_ignores_session_username(source):
    ctx = console(source)
    ctx.user.username = '<img src=x onerror="alert(1)">'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        response = await client.get("/admin/")
    assert response.status_code == 200
    assert "&lt;img" in response.text and "<img src=x" not in response.text


@pytest.mark.asyncio
async def test_role_list_has_an_edit_link_for_every_record_for_platform_owner(source):
    ctx = console(source, actor("superuser"))
    rows = [
        source.PlatformRole(id=pk, codename=codename, description=codename)
        for pk, codename in [(1, "user"), (6, "admin"), (128, "operator")]
    ]
    ctx.admin._views[0].list = AsyncMock(return_value=Pagination(rows=rows, page=1, page_size=50, count=3))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        response = await client.get("/admin/platform-role/list")
    assert response.status_code == 200, response.text
    for role in rows:
        assert f'/admin/platform-role/edit/{role.id}"' in response.text


@pytest.mark.asyncio
async def test_missing_role_is_not_written(source):
    ctx = console(source, actor("superuser"))
    ctx.target.get.return_value = None
    with pytest.raises(HTTPException) as exc:
        await ctx.admin._views[0].update_model(request_for(ctx), "999999", {"permissions": []})
    assert exc.value.status_code == 404
    ctx.target.set_permissions.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_choice_labels_include_names_and_preserve_escaped_native_values(source):
    ctx = console(source, actor("superuser"))
    resource = source.canonical_model_type(
        id=1, resource_key="agent.message", model_name="message", table_name="messages"
    )
    name = 'Чтение <script>alert("not-executed")</script>'
    permission = source.Permission(id=9, name=name, model_type=resource, action_type=TYPES.ActionType.VIEW)
    source.Permission.objects.prefetch_related.return_value.all.return_value = [permission]
    ctx.target.get.return_value.permissions = [permission]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        response = await client.get("/admin/platform-role/edit/2")
    assert response.status_code == 200
    assert '<option selected value="9">agent.message.view — Чтение &lt;script&gt;' in response.text
    assert '<script>alert("not-executed")</script>' not in response.text
    assert 'name="csrf_token"' in response.text
    assert 'value="Save and continue editing"' in response.text
    source.Permission.objects.prefetch_related.assert_called_once_with("model_type")
    source.Permission.objects.prefetch_related.return_value.all.assert_awaited_once()
    ctx.target.set_permissions.assert_not_awaited()
