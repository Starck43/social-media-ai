"""Real source policy on controlled in-memory grants; NOT DB/ASGI acceptance.

Only identity/storage, CSRF and final action dispatch are explicit doubles.
User permission method bodies, resource catalog, SQLAdmin grant backend and
unified POST guard are executed unchanged. No app/settings/engine import.
"""

import ast
import builtins
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from source_import_isolation import load_isolated_source
from sqladmin.authorization import Action, custom_action
from test_admin_model_probe import extracted_handler

ROOT = Path(__file__).resolve().parents[1]
DB_ENUMS = load_isolated_source(
    "_policy_enum_helpers",
    ROOT / "app/utils/db_enums.py",
    {"app.core.config": NS(settings=NS(DB_SCHEMA="synthetic_no_database"))},
)
TYPES = load_isolated_source(
    "_policy_identity_types",
    ROOT / "app/types/enums/user_types.py",
    {"app.utils.db_enums": DB_ENUMS},
)
ActionType, PlatformRoleType = TYPES.ActionType, TYPES.PlatformRoleType
CATALOG = load_isolated_source(
    "_policy_real_resource_catalog",
    ROOT / "app/core/access_catalog.py",
    {"app.types": TYPES},
)


def policy_user_class():
    """Keep real method bodies, omit only mapped fields/ORM class construction."""
    tree = ast.parse((ROOT / "app/models/identity/user.py").read_text())
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "User")
    names = {"is_platform_admin", "model_permissions", "has_perm_for", "has_admin_access"}
    methods = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == len(names)
    node = ast.ClassDef(name="PolicyUser", bases=[], keywords=[], body=methods, decorator_list=[])
    code = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    normal_import = builtins.__import__

    def imports(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.core.access_catalog":
            return CATALOG
        return normal_import(name, globals, locals, fromlist, level)

    ns = {
        "ActionType": ActionType,
        "PlatformRoleType": PlatformRoleType,
        "__builtins__": dict(vars(builtins), __import__=imports),
    }
    exec(compile(code, str(ROOT / "app/models/identity/user.py"), "exec"), ns)
    return ns["PolicyUser"]


def user_with(*actions, role="operator", active=True, resource="llm.model"):
    user = policy_user_class()()
    user.id, user.is_active, user._model_perms_cache = 7, active, None
    user.role = NS(
        codename=role, permissions=[NS(action_type=action, model_type=NS(resource_key=resource)) for action in actions]
    )
    return user


def console(user):
    manager = NS(get=AsyncMock(return_value=user))
    manager.prefetch_related = Mock(return_value=manager)
    identity = AsyncMock(return_value=NS(id=7, is_active=True))
    module = load_isolated_source(
        "_admin_model_real_permission_backend",
        ROOT / "app/admin/authorization.py",
        {
            "app.models": NS(User=NS(objects=manager)),
            "app.types": TYPES,
            "app.services.user.auth": NS(get_authenticated_user=identity),
        },
    )
    # Read the real view's declaration; fail if it drifts away from CONFIGURE.
    tree = ast.parse((ROOT / "app/admin/views.py").read_text())
    view_node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LLMModelAdmin")
    declaration = next(
        n
        for n in view_node.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "action_permissions" for t in n.targets)
    )
    namespace = {"ActionType": ActionType}
    exec(compile(ast.Module(body=[declaration], type_ignores=[]), "<real-view-declaration>", "exec"), namespace)
    assert namespace["action_permissions"] == {"test-model": ActionType.CONFIGURE}

    class View:
        identity = "llm-model"
        model = type("LLMModel", (), {})
        action_permissions = namespace["action_permissions"]

        def test_model_action(self):
            pass

        test_model_action._slug = "test-model"

    backend = module.AdminAuthorizationBackend()
    admin = NS(_views=[View()], authorization_backend=backend)
    backend.setup(admin)

    def request():
        return NS(
            state=NS(),
            session={"token": "synthetic-session"},
            app=NS(state=NS(admin=admin)),
            form=AsyncMock(return_value={"test_type": "short", "model_id": "7", "csrf_token": "synthetic"}),
        )

    return module, backend, request, manager, identity


def unified_post(module):
    submitted = AsyncMock(return_value="executed-local-check")
    verify = Mock(return_value=True)
    handler = extracted_handler(
        "app/admin/endpoints.py",
        "submit_llm_model_test",
        {
            "require_admin_perm": module.require_admin_perm,
            "ActionType": ActionType,
            "HTTPException": HTTPException,
            "settings": NS(PERSONAL_BILLING_ENABLED=False),
            "get_csrf_manager": lambda request: NS(verify_token=verify),
        },
        {"app.admin.actions": NS(LLMModelActions=NS(submit_test=submitted))},
    )
    return handler, submitted, verify


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actions,can_list,can_test",
    [
        ([], False, False),
        ([ActionType.VIEW], True, False),
        ([ActionType.CONFIGURE], True, True),
        ([ActionType.VIEW, ActionType.CONFIGURE], True, True),
        ([ActionType.UPDATE], True, False),
        ([ActionType.VIEW, ActionType.UPDATE], True, False),
    ],
)
async def test_real_policy_separates_list_custom_get_and_unified_post(actions, can_list, can_test):
    user = user_with(*actions)
    module, backend, request, manager, identity = console(user)
    req = request()
    await backend.load(req)
    assert backend.has_permission(req, "llm-model", Action.LIST) is can_list
    assert backend.has_permission(req, "llm-model", Action.DETAILS) is can_list
    assert backend.has_permission(req, "llm-model", custom_action("test-model")) is can_test
    assert user.has_perm_for("llmmodel", ActionType.CONFIGURE) is can_test
    handler, submitted, verify = unified_post(module)
    post = request()
    if can_test:
        assert await handler(post) == "executed-local-check"
        submitted.assert_awaited_once()
        verify.assert_called_once_with("synthetic")
        post.form.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as error:
            await handler(post)
        assert error.value.status_code == 403
        submitted.assert_not_awaited()
        verify.assert_not_called()
        post.form.assert_not_awaited()
    assert manager.get.await_count == identity.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("role,active", [("user", True), ("operator", False), ("admin", False)])
async def test_workspace_identity_and_disabled_roles_never_inherit_configuration(role, active):
    module, backend, request, _, _ = console(user_with(ActionType.CONFIGURE, role=role, active=active))
    req = request()
    await backend.load(req)
    assert not backend.has_permission(req, "llm-model", custom_action("test-model"))
    handler, submitted, _ = unified_post(module)
    with pytest.raises(HTTPException) as error:
        await handler(request())
    assert error.value.status_code == 403
    submitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_resource_grant_does_not_grant_model_configuration():
    module, backend, request, _, _ = console(user_with(ActionType.CONFIGURE, resource="llm.provider"))
    req = request()
    await backend.load(req)
    assert not backend.has_permission(req, "llm-model", custom_action("test-model"))
    handler, submitted, _ = unified_post(module)
    with pytest.raises(HTTPException) as error:
        await handler(request())
    assert error.value.status_code == 403
    submitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_between_requests_is_not_cached_across_requests():
    module, backend, request, manager, identity = console(user_with(ActionType.VIEW, ActionType.CONFIGURE))
    first = request()
    await backend.load(first)
    assert backend.has_permission(first, "llm-model", custom_action("test-model"))
    manager.get.return_value = user_with(ActionType.VIEW)
    second = request()
    await backend.load(second)
    assert backend.has_permission(second, "llm-model", Action.LIST)
    assert not backend.has_permission(second, "llm-model", custom_action("test-model"))
    handler, submitted, _ = unified_post(module)
    with pytest.raises(HTTPException) as error:
        await handler(request())
    assert error.value.status_code == 403
    submitted.assert_not_awaited()
    assert manager.get.await_count == identity.await_count == 3


@pytest.mark.asyncio
async def test_configure_does_not_bypass_csrf():
    module, _, request, _, _ = console(user_with(ActionType.CONFIGURE))
    handler, submitted, verify = unified_post(module)
    verify.return_value = False
    with pytest.raises(HTTPException) as error:
        await handler(request())
    assert error.value.status_code == 403
    submitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_resolution_failure_denies_without_dispatch():
    module, backend, request, manager, _ = console(user_with(ActionType.CONFIGURE))
    manager.get.side_effect = RuntimeError("synthetic storage unavailable")
    req = request()
    await backend.load(req)
    assert not backend.has_permission(req, "llm-model", custom_action("test-model"))
    handler, submitted, _ = unified_post(module)
    with pytest.raises(HTTPException) as error:
        await handler(request())
    assert error.value.status_code == 403
    submitted.assert_not_awaited()
