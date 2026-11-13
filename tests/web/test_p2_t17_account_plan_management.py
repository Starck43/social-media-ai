"""Account tariff gates and atomic onboarding without PostgreSQL or providers."""

from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.models import ServicePlanPolicy, Tenant, User, UserServicePlan
from app.models.managers.tenant_manager import PlanLimitError, tenants
from app.services.tenancy.capabilities import CAPABILITIES
from app.services.tenancy.entitlements import decide
from app.services.tenancy.service_plan_policy import ServicePlanPolicyError
from app.web import auth
from app.web import settings as web_settings

__source_only__ = True


class SessionContext:
    def __init__(self):
        self.errors = []

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.errors.append(exc)


def request(*, superuser=False, role="owner"):
    return NS(
        state=NS(web_user=NS(id=900001, is_superuser=superuser), tenant_id=17, workspace_role=role),
        session={},
    )


def policy_workspace():
    policy = ServicePlanPolicy(
        plan_code="starter",
        policy_version=7,
        module_grants=[],
        daily_quotas={},
        resource_limits=deepcopy(Tenant.PLAN_LIMITS["starter"]),
        owned_workspace_limit_configured=True,
        max_owned_workspaces=0,
        token_policy_configured=False,
        token_unit=None,
        token_period=None,
        token_limit=None,
    )
    plan = UserServicePlan(
        user_id=501,
        plan_code="starter",
        policy_version=7,
        is_active=True,
        policy=policy,
        holder_user=User(id=501, is_active=True),
    )
    return Tenant(
        id=17,
        plan="business",
        plan_holder_user_id=501,
        is_active=True,
        service_plan=plan,
        max_sources=100,
        daily_cost_limit=100,
    )


def test_cutover_is_off_by_default():
    assert type(settings).model_fields["USER_SERVICE_PLANS_ENABLED"].default is False


def test_enabled_limits_and_modules_ignore_legacy_mirror(monkeypatch):
    tenant = policy_workspace()
    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    assert tenant.plan_limits()["max_sources"] == 3
    assert tenant.effective_limits()["max_sources"] == 3
    assert not decide(tenant, [next(iter(CAPABILITIES))]).allowed
    tenant.plan = "pro"
    assert tenant.plan_limits()["max_sources"] == 3
    tenant.service_plan.holder_user.is_active = False
    with pytest.raises(ServicePlanPolicyError):
        tenant.plan_limits()
    assert not decide(tenant, [next(iter(CAPABILITIES))]).allowed


def test_enabled_missing_policy_never_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    tenant = Tenant(id=17, plan="business", is_active=True)
    with pytest.raises(ServicePlanPolicyError):
        tenant.plan_limits()
    assert not decide(tenant, [next(iter(CAPABILITIES))]).allowed
    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", False)
    assert tenant.plan_limits() == Tenant.PLAN_LIMITS["business"]


@pytest.mark.parametrize("role", ["owner", "admin", "manager", "analyst", "viewer"])
async def test_workspace_role_cannot_change_host_tariff(monkeypatch, role):
    monkeypatch.setattr(web_settings, "ensure_csrf", lambda *_: True)
    lookup = AsyncMock()
    monkeypatch.setattr(tenants, "get", lookup)
    req = request(role=role)
    response = await web_settings.plan_update(req, "business", "csrf", tenant_id=999, policy_version=7)
    assert response.status_code == 302
    lookup.assert_not_awaited()
    assert req.session


async def test_plan_csrf_precedes_any_lookup(monkeypatch):
    monkeypatch.setattr(web_settings, "ensure_csrf", lambda *_: False)
    lookup = AsyncMock()
    monkeypatch.setattr(tenants, "get", lookup)
    await web_settings.plan_update(request(superuser=True), "pro", "wrong", tenant_id=999, policy_version=7)
    lookup.assert_not_awaited()


async def test_enabled_plan_assignment_uses_session_sponsorship_and_preserves_state(monkeypatch):
    from app.core import database
    from app.services.tenancy import service_plan_management

    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    monkeypatch.setattr(web_settings, "ensure_csrf", lambda *_: True)
    lookup = AsyncMock(return_value=NS(id=17, plan_holder_user_id=501))
    monkeypatch.setattr(tenants, "get", lookup)
    session = SessionContext()
    monkeypatch.setattr(database, "async_session_maker", lambda: session)
    assign = AsyncMock()
    monkeypatch.setattr(service_plan_management, "assign_user_plan", assign)
    req = request(superuser=True)
    response = await web_settings.plan_update(req, "starter", "csrf", tenant_id=999, policy_version=7)
    assert response.status_code == 302
    lookup.assert_awaited_once_with(id=17)
    assign.assert_awaited_once_with(
        session,
        actor_user_id=900001,
        user_id=501,
        plan_code="starter",
        policy_version=7,
        preserve_state=True,
    )
    assert all(error is None for error in session.errors)


@pytest.mark.parametrize("version", [None, 0, -1, True])
async def test_plan_write_requires_explicit_revision(monkeypatch, version):
    from app.services.tenancy import service_plan_management

    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    monkeypatch.setattr(web_settings, "ensure_csrf", lambda *_: True)
    monkeypatch.setattr(tenants, "get", AsyncMock(return_value=NS(id=17, plan_holder_user_id=501)))
    assign = AsyncMock()
    monkeypatch.setattr(service_plan_management, "assign_user_plan", assign)
    response = await web_settings.plan_update(request(superuser=True), "pro", "csrf", None, version)
    assert response.status_code == 302
    assign.assert_not_awaited()


async def test_fresh_operator_denial_does_not_commit_assignment(monkeypatch):
    from app.core import database
    from app.services.tenancy import service_plan_management

    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    monkeypatch.setattr(web_settings, "ensure_csrf", lambda *_: True)
    monkeypatch.setattr(tenants, "get", AsyncMock(return_value=NS(id=17, plan_holder_user_id=501)))
    session = SessionContext()
    monkeypatch.setattr(database, "async_session_maker", lambda: session)
    monkeypatch.setattr(service_plan_management, "assign_user_plan", AsyncMock(side_effect=PermissionError))
    response = await web_settings.plan_update(request(superuser=True), "pro", "csrf", None, 7)
    assert response.status_code == 302
    assert any(isinstance(error, PermissionError) for error in session.errors)


async def test_workspace_helper_never_splits_membership_transaction(monkeypatch):
    monkeypatch.setattr(tenants, "get", AsyncMock(return_value=None))
    create = AsyncMock(return_value=NS(id=17))
    monkeypatch.setattr(tenants, "create_owned_workspace", create)
    session = SessionContext()
    assert await auth._create_workspace(NS(id=501, username="holder"), "Workspace", session=session) == 17
    create.assert_awaited_once_with(user_id=501, name="Workspace", slug="workspace", session=session)


async def test_failed_registration_rolls_back_account_and_issues_no_token(monkeypatch):
    from app.core import database

    monkeypatch.setattr(auth, "ensure_csrf", lambda *_: True)
    monkeypatch.setattr(User.objects, "get_by_username_or_email", AsyncMock(return_value=None))
    monkeypatch.setattr(User.objects, "exists", AsyncMock(return_value=False))
    monkeypatch.setattr(auth.Role.objects, "get", AsyncMock(return_value=NS(id=3)))
    create_user = AsyncMock(return_value=NS(id=501))
    monkeypatch.setattr(User.objects, "create_user", create_user)
    create_workspace = AsyncMock(side_effect=PlanLimitError("No account policy"))
    monkeypatch.setattr(auth, "_create_workspace", create_workspace)
    monkeypatch.setattr(auth, "render", lambda *_, **kw: NS(status_code=kw["status_code"]))
    session = SessionContext()
    monkeypatch.setattr(database, "async_session_maker", lambda: session)
    req = request()
    response = await auth.register_submit(req, "new-user", "new@example.test", "password-123", "Work", "csrf")
    assert response.status_code == 409
    assert not req.session
    assert create_user.await_args.kwargs["session"] is session
    assert create_workspace.await_args.kwargs["session"] is session
    assert any(isinstance(error, PlanLimitError) for error in session.errors)


async def test_legacy_manager_calls_do_not_pass_none_session_to_decorator(monkeypatch):
    from app.models.managers.base_manager import BaseManager

    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", False)
    create, update = AsyncMock(), AsyncMock()
    monkeypatch.setattr(BaseManager, "create", create)
    monkeypatch.setattr(BaseManager, "update_by_id", update)
    await tenants.create(name="Legacy", slug="legacy")
    create.assert_awaited_once_with(name="Legacy", slug="legacy")
    await tenants.update_by_id(17, name="Renamed")
    update.assert_awaited_once_with(17, name="Renamed")


async def test_enabled_generic_workspace_tariff_writes_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    with pytest.raises(PlanLimitError):
        await tenants.create(name="Forged", slug="forged", plan="business")
    with pytest.raises(ValueError):
        await tenants.update_by_id(17, plan="business")
    with pytest.raises(ValueError):
        await tenants.update_by_id(17, plan_holder_user_id=900001)


async def test_admin_cannot_create_or_forge_sponsorship(monkeypatch):
    from fastapi import HTTPException

    from app.admin.view_modules.users import TenantAdmin

    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    view = TenantAdmin()
    with pytest.raises(HTTPException) as error:
        await view.insert_model(request(superuser=True), {"name": "forged"})
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await view.update_model(request(superuser=True), "17", {"plan_holder_user_id": 900001})
    assert error.value.status_code == 422


@pytest.mark.parametrize("requested", ["pro", "business"])
async def test_admin_broad_role_cannot_assign_account_tariff(monkeypatch, requested):
    from fastapi import HTTPException

    from app.admin import csrf
    from app.admin.view_modules.users import TenantAdmin
    from app.core import database

    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    view = TenantAdmin()
    fresh = NS(id=900001, is_superuser=False, has_perm_for=lambda *_: True)
    view._admin_ref = NS(authorization_backend=NS(resolve_user=AsyncMock(return_value=fresh)))
    query = NS(get=AsyncMock(return_value=fresh))
    monkeypatch.setattr(User.objects, "prefetch_related", lambda *_: query)
    monkeypatch.setattr(csrf, "verify_request_token", lambda *_: True)
    session = SessionContext()
    monkeypatch.setattr(database, "async_session_maker", lambda: session)
    monkeypatch.setattr(tenants, "get", AsyncMock(return_value=NS(id=17, plan="pro", plan_holder_user_id=501)))
    update = AsyncMock()
    monkeypatch.setattr(tenants, "update_by_id", update)
    req = request(role="admin")
    req.form = AsyncMock(return_value={"csrf_token": "csrf"})
    with pytest.raises(HTTPException) as error:
        await view.update_model(req, "17", {"plan": requested, "policy_version": 7})
    assert error.value.status_code == 403
    update.assert_not_awaited()


@pytest.mark.parametrize("consume", [False, True])
@pytest.mark.parametrize("reason", ["plan_expired", "holder_inactive", "missing_policy"])
async def test_holder_policy_denial_is_terminal_and_never_records_usage(monkeypatch, consume, reason):
    from app.core import permissions
    from app.models.managers.entitlement_manager import EntitlementManager
    from app.models.managers.job_manager import JobManager
    from app.services.tenancy import service_plan_policy
    from app.services.tenancy.entitlements import EntitlementDenied

    monkeypatch.setattr(settings, "USER_SERVICE_PLANS_ENABLED", True)
    monkeypatch.setattr(EntitlementManager, "_tenant_id", staticmethod(lambda: 17))
    monkeypatch.setattr(JobManager, "lock_source_admission", AsyncMock())
    monkeypatch.setattr(permissions, "get_current_user", lambda: None)
    monkeypatch.setattr(
        service_plan_policy, "load_workspace_policy", AsyncMock(side_effect=ServicePlanPolicyError(reason))
    )
    session = NS(
        execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: NS(id=17, is_active=True))),
        add=AsyncMock(),
        flush=AsyncMock(),
    )
    key = next(iter(CAPABILITIES))
    if consume:
        with pytest.raises(EntitlementDenied) as error:
            await EntitlementManager().evaluate(session, [key], job_id=99, consume=True)
        decision = error.value.decision
    else:
        decision = await EntitlementManager().evaluate(session, [key])
    assert not decision.allowed
    assert decision.denials == ({"capability": key, "reason": reason},)
    assert session.execute.await_count == 1
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
