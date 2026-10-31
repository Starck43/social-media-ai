"""Live SQLAdmin acceptance for the selected platform-owner deletion policy.

Run with root conftest against the serialized owner-local ``test_schema`` only.
This creates uniquely named synthetic accounts/workspaces and restores the
admin role's canonical grant set. It performs no app lifespan or provider call.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import sys
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

# Guard before any app imports or engine creation.
_expected_conftest = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected_conftest)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: root conftest must redirect to test_schema; --noconftest is forbidden")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in {"localhost", "127.0.0.1"}
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only localhost:5432/social_manager/test_schema is approved")

from app.admin.csrf import SESSION_ID_KEY
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import ActionAuditEvent, ModelType, Permission, PlatformRole, Tenant, TenantUser, User, WorkspaceRole
from app.types import ActionType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
PASSWORD = "Synthetic-P11-T28-Console!"
CSRF = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


@contextmanager
def platform_scope():
    """Trusted synthetic setup/cleanup only; never wraps an ASGI request."""
    with tenant_scope(bypass=True), operator_permission_scope():
        yield


@asynccontextmanager
async def admin_client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        yield client


def session_id(app, client):
    from starlette.middleware.sessions import SessionMiddleware

    signer = SessionMiddleware(app, secret_key=settings.SECRET_KEY).signer
    session = json.loads(base64.b64decode(signer.unsign(client.cookies["session"].encode())))
    return session[SESSION_ID_KEY]


def token_from(html):
    match = CSRF.search(html)
    assert match, "Console form must contain its CSRF token"
    return match.group(1)


async def login(client, user):
    page = await client.get("/admin/login")
    assert page.status_code == 200, page.text
    response = await client.post(
        "/admin/login",
        data={"username": user.username, "password": PASSWORD, "csrf_token": token_from(page.text)},
    )
    assert response.status_code in {302, 303}, f"Real console login failed: {response.status_code}: {response.text}"


@pytest.fixture
def live_admin_app(monkeypatch):
    """Create the actual app without lifespan/runtime, confined to loopback DB."""
    monkeypatch.setattr(settings, "ADMIN_ENABLED", True)
    forbidden = []
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guard(original):
        def checked(sock, address):
            if sock.family in {socket.AF_INET, socket.AF_INET6}:
                if address[0] not in {"localhost", "127.0.0.1", "::1"} or address[1] != 5432:
                    forbidden.append(f"{address[0]}:{address[1]}")
                    raise AssertionError("Only approved loopback PostgreSQL is allowed")
            return original(sock, address)

        return checked

    monkeypatch.setattr(socket.socket, "connect", guard(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(original_connect_ex))
    yield create_application()
    assert not forbidden, f"Forbidden network attempts: {forbidden}"


@pytest.fixture
async def live_records():
    """Own synthetic console rows, one protected audit event, and role-grant restoration."""
    marker = uuid4().hex[:12]
    users: list[int] = []
    tenants: list[int] = []
    audit_event_ids: list[int] = []
    transient_permission_id = None
    original_admin_grants = None
    admin_role_id = None
    retained = None
    try:
        with platform_scope():
            admin_role = await PlatformRole.objects.get_by_codename("admin")
            assert admin_role is not None, "Expected seeded admin role; this test never seeds platform roles"
            admin_role_id = admin_role.id
            loaded_admin_role = await PlatformRole.objects.get_with_permissions(admin_role.id)
            original_admin_grants = sorted(permission.id for permission in loaded_admin_role.permissions)

            model_type = await ModelType.objects.get(resource_key="tenancy.workspace")
            assert model_type is not None, "Expected canonical tenancy.workspace model type"
            delete_permission = await Permission.objects.get_by_codename("tenancy.workspace.delete")
            if delete_permission is None:
                # This is the one newly declared catalog row under test; remove
                # it at teardown if the checked-out test schema predates it.
                delete_permission = await Permission.objects.create_permission(
                    "tenancy.workspace.delete",
                    "Delete workspace (temporary acceptance grant)",
                    ActionType.DELETE,
                    model_type.id,
                )
                transient_permission_id = delete_permission.id
            all_permissions = await Permission.objects.all()
            await PlatformRole.objects.set_permissions(admin_role.id, [p.id for p in all_permissions])

            actor_role = await PlatformRole.objects.get_by_codename("superuser")
            assert actor_role is not None, "Expected seeded superuser role"
            synthetic_admin = await User.objects.create_user(
                username=f"p11t28-admin-{marker}",
                email=f"p11t28-admin-{marker}@example.invalid",
                password=PASSWORD,
                role_id=admin_role.id,
                is_active=True,
            )
            users.append(synthetic_admin.id)
            synthetic_owner = await User.objects.create_user(
                username=f"p11t28-owner-{marker}",
                email=f"p11t28-owner-{marker}@example.invalid",
                password=PASSWORD,
                role_id=actor_role.id,
                is_active=True,
            )
            users.append(synthetic_owner.id)

            blocked = await Tenant.objects.create(
                name=f"P11-T28 admin denial {marker}", slug=f"p11-t28-deny-{marker}", plan="starter"
            )
            tenants.append(blocked.id)
            workspace_admin = await WorkspaceRole.objects.get(codename="admin")
            assert workspace_admin is not None, "Expected seeded workspace admin role"
            await TenantUser.objects.add_web_member(
                tenant_id=blocked.id,
                user_id=synthetic_admin.id,
                role_id=workspace_admin.id,
            )
            removable = await Tenant.objects.create(
                name=f"P11-T28 owner deletion {marker}", slug=f"p11-t28-delete-{marker}", plan="starter"
            )
            tenants.append(removable.id)
            retained = await Tenant.objects.create(
                name=f"P11-T28 retained audit {marker}", slug=f"p11-t28-audit-{marker}", plan="starter"
            )
            tenants.append(retained.id)
        # Exercise the existing immutable audit FK contract using the supported
        # manager path; no direct audit-table writes or trigger bypasses.
        with tenant_scope(retained.id):
            async with async_session_maker() as session:
                audit_event = await ActionAuditEvent.objects.record_success(
                    session=session,
                    actor_kind="user",
                    actor_id=synthetic_owner.id,
                    action="scheduling.task_admit",
                    resource_kind="agent_task",
                    resource_id=1,
                )
                await session.commit()
        audit_event_ids.append(audit_event.id)
        yield {
            "admin": synthetic_admin,
            "owner": synthetic_owner,
            "admin_workspace_id": blocked.id,
            "admin_workspace_slug": blocked.slug,
            "owner_workspace_id": removable.id,
            "audit_workspace_id": retained.id,
            "audit_event_id": audit_event.id,
            "permission_id": transient_permission_id,
        }
    finally:
        with platform_scope():
            # Keep cleanup deterministic after the successful owner deletion,
            # and restore the canonical platform-admin role exactly.
            if admin_role_id is not None and original_admin_grants is not None:
                await PlatformRole.objects.set_permissions(admin_role_id, original_admin_grants)
            for tenant_id in tenants:
                if retained is not None and tenant_id == retained.id and audit_event_ids:
                    # The event is intentionally retained during the owner
                    # delete attempt. Teardown expires only our fixture event
                    # using the existing workspace retention mechanism.
                    with tenant_scope(tenant_id):
                        expired = await ActionAuditEvent.objects.delete_expired(
                            now=datetime.now(timezone.utc) + timedelta(days=8)
                        )
                    assert expired == 1
                if await Tenant.objects.get(id=tenant_id) is not None:
                    await Tenant.objects.delete_by_id(tenant_id)
            for user_id in users:
                user = await User.objects.prefetch_related("role").get(id=user_id)
                if user is not None:
                    if user.is_superuser:
                        admin_role = await PlatformRole.objects.get_by_codename("admin")
                        await User.objects.update_by_id(user_id, role_id=admin_role.id)
                    await User.objects.delete_user(user_id)
            if transient_permission_id is not None:
                await Permission.objects.delete_by_id(transient_permission_id)


async def test_live_console_limits_workspace_delete_to_platform_owner(live_admin_app, live_records):
    """Login/list/delete execute through SQLAdmin, CSRF, auth and real DB sessions."""
    async with admin_client(live_admin_app) as admin_client_:
        await login(admin_client_, live_records["admin"])
        listing = await admin_client_.get("/admin/tenant/list")
        assert listing.status_code == 200, listing.text
        assert (
            live_records["admin_workspace_slug"] in listing.text
        ), "Fully granted admin must see its selected workspace"
        details = await admin_client_.get(f"/admin/tenant/details/{live_records['admin_workspace_id']}")
        assert details.status_code == 200, details.text
        admin_token = live_admin_app.state.csrf_manager.generate_token(
            session_id=session_id(live_admin_app, admin_client_)
        )
        denied = await admin_client_.delete(
            "/admin/tenant/delete",
            params={"pks": live_records["admin_workspace_id"]},
            headers={"X-CSRF-Token": admin_token},
        )

    assert (
        denied.status_code == 403
    ), f"Non-owner workspace deletion must be clean 403: {denied.status_code} {denied.text}"
    with platform_scope():
        assert await Tenant.objects.get(id=live_records["admin_workspace_id"]) is not None

    async with admin_client(live_admin_app) as owner_client:
        await login(owner_client, live_records["owner"])
        listing = await owner_client.get("/admin/tenant/list")
        assert listing.status_code == 200, listing.text
        owner_token = live_admin_app.state.csrf_manager.generate_token(
            session_id=session_id(live_admin_app, owner_client)
        )
        deleted = await owner_client.delete(
            "/admin/tenant/delete",
            params={"pks": live_records["owner_workspace_id"]},
            headers={"X-CSRF-Token": owner_token},
        )

    assert deleted.status_code == 200, deleted.text
    assert "error=" not in deleted.text, f"Owner console deletion failed: {deleted.text}"
    with platform_scope():
        assert await Tenant.objects.get(id=live_records["owner_workspace_id"]) is None

    async with admin_client(live_admin_app) as owner_client:
        await login(owner_client, live_records["owner"])
        owner_token = live_admin_app.state.csrf_manager.generate_token(
            session_id=session_id(live_admin_app, owner_client)
        )
        protected = await owner_client.delete(
            "/admin/tenant/delete",
            params={"pks": live_records["audit_workspace_id"]},
            headers={"X-CSRF-Token": owner_token},
        )

    assert protected.status_code < 500, protected.text
    assert "error=" in protected.text, f"Retained audit should refuse deletion through SQLAdmin: {protected.text}"
    with platform_scope():
        assert await Tenant.objects.get(id=live_records["audit_workspace_id"]) is not None
    with tenant_scope(live_records["audit_workspace_id"]):
        event = await ActionAuditEvent.objects.get(id=live_records["audit_event_id"])
    assert event is not None, "Refused workspace deletion must preserve retained audit evidence"
    assert "IntegrityError" not in protected.text
    assert "foreign key" not in protected.text.lower()
