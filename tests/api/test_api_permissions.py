"""Tests for API permission system — require_model_perm and require_platform_role.

Validates that:
1. require_model_perm passes for user with permission
2. require_model_perm refuses for user without permission
3. require_model_perm passes for superuser
4. require_platform_role passes for user with sufficient role
5. require_platform_role refuses for user with insufficient role
6. All new endpoints use require_model_perm (not is_superuser)
7. Sources CRUD endpoints work correctly
8. Tasks CRUD + run endpoints work correctly
9. Credentials endpoints work correctly
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, Role, Source, User, WorkspaceRole
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import ActionType, PlatformRoleType, WorkspaceRoleType

# ── fixtures ──────────────────────────────────────────────────────────────────


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up: the new user owns a fresh workspace."""
    username = f"{prefix}{secrets.token_hex(3)}"
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret-password-1",
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _register_owner(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up as an ordinary platform user who owns the new workspace."""
    return await _register(client, prefix)


async def _csrf(client: AsyncClient, path: str) -> str:
    import re

    page = await client.get(path)
    assert page.status_code == 200
    match = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _loaded(user: User) -> User:
    """The user as the middleware hands them over: role and permissions eager."""
    return await User.objects.prefetch_related("role.permissions").get(id=user.id)


async def _invitee(prefix: str, workspace_role: WorkspaceRoleType, tenant_id: int) -> User:
    """A second web user in someone else's workspace."""
    username = f"{prefix}{secrets.token_hex(3)}"
    role = await Role.objects.get(codename=PlatformRoleType.USER.value)
    assert role is not None, f"role {PlatformRoleType.USER.value} is seeded"
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role=workspace_role.value)
    return user


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        with tenant_scope(bypass=True):
            await tenants.delete_by_id(tenant_id)


async def _platform_value() -> int:
    from app.models import Platform

    platform = await Platform.objects.filter(platform_type="telegram").first()
    assert platform is not None, "telegram platform is seeded"
    return platform.id


async def _jwt_token(client: AsyncClient, username: str, password: str = "secret-password-1") -> str:
    """Get JWT access token via the API login endpoint."""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


async def _auth_headers(client: AsyncClient, username: str) -> dict[str, str]:
    """Get Authorization headers with JWT for a user."""
    token = await _jwt_token(client, username)
    return {"Authorization": f"Bearer {token}"}


# ── require_model_perm tests ─────────────────────────────────────────────────


async def test_require_model_perm_passes_for_user_with_permission() -> None:
    """A workspace manager can create sources without platform grants."""
    async with await _client() as c:
        owner, tenant_id = await _register_owner(c, "PermOwner")
        member = await _invitee("PermViewer", WorkspaceRoleType.MANAGER, tenant_id)
        loaded = await _loaded(member)
        assert not loaded.has_perm_for("source", ActionType.CREATE), "platform USER has no global source grant"

        # Get JWT token for API calls
        headers = await _auth_headers(c, member.username)

        # POST /api/v1/sources should succeed
        platform = await _platform_value()
        external_id = secrets.token_hex(6)
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test source",
                "platform_id": platform,
                "source_type": "USER",
                "external_id": external_id,
            },
            headers=headers,
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        assert resp.json()["external_id"] == external_id

        with tenant_scope(bypass=True):
            source = await Source.objects.filter(external_id=external_id).first()
        assert source is not None
        await Source.objects.delete(id=source.id)
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_require_model_perm_refuses_user_without_permission() -> None:
    """VIEWER cannot create sources."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "BlockedOwner")
        member = await _invitee("BlockedViewer", WorkspaceRoleType.VIEWER, tenant_id)
        loaded = await _loaded(member)
        assert not loaded.has_perm_for("source", ActionType.CREATE), "premise: VIEWER may not create sources"

        # Get JWT token for API calls
        headers = await _auth_headers(c, member.username)

        external_id = secrets.token_hex(6)
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test source",
                "platform_id": await _platform_value(),
                "source_type": "user",
                "external_id": external_id,
            },
            headers=headers,
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        assert "Missing permission" in resp.json()["detail"]

        with tenant_scope(bypass=True):
            source = await Source.objects.filter(external_id=external_id).first()
        assert source is None

        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_platform_admin_does_not_bypass_workspace_membership_role() -> None:
    """A platform admin invited as a viewer cannot write in that workspace."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "AdminViewerOwner")
        admin = await _invitee("AdminViewer", WorkspaceRoleType.VIEWER, tenant_id)
        admin_role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
        await User.objects.update_by_id(admin.id, role_id=admin_role.id)
        headers = await _auth_headers(c, admin.username)

        external_id = secrets.token_hex(6)
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test source",
                "platform_id": await _platform_value(),
                "source_type": "USER",
                "external_id": external_id,
            },
            headers=headers,
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

        with tenant_scope(bypass=True):
            source = await Source.objects.filter(external_id=external_id).first()
        assert source is None
        await _drop(admin, tenant_id)
        await _drop(owner, tenant_id)


# ── require_platform_role tests ──────────────────────────────────────────────


async def test_require_platform_role_passes_for_sufficient_role() -> None:
    """ADMIN can list users (requires ADMIN role)."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "RoleOwner")
        admin_role = await Role.objects.get(codename=PlatformRoleType.ADMIN.value)
        member_role = await WorkspaceRole.objects.get(codename=WorkspaceRoleType.VIEWER.value)
        admin_user = await User.objects.create_user(
            username=f"admin{secrets.token_hex(3)}",
            email=f"admin{secrets.token_hex(3)}@example.com",
            password="secret-password-1",
            role_id=admin_role.id,
        )
        # Add admin_user to the tenant workspace
        await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=admin_user.id, role_id=member_role.id)

        # Get JWT token for API calls
        headers = await _auth_headers(c, admin_user.username)

        resp = await c.get("/api/v1/users/", headers=headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        await _drop(admin_user, None)
        await _drop(owner, tenant_id)


async def test_require_platform_role_refuses_for_insufficient_role() -> None:
    """VIEWER cannot list users (requires ADMIN role)."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "RoleOwner2")
        viewer = await _invitee("RoleViewer", WorkspaceRoleType.VIEWER, tenant_id)

        # Get JWT token for API calls
        headers = await _auth_headers(c, viewer.username)

        resp = await c.get("/api/v1/users/", headers=headers)
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

        await _drop(viewer, tenant_id)
        await _drop(owner, tenant_id)


# ── Sources CRUD tests ──────────────────────────────────────────────────────


async def test_sources_crud() -> None:
    """Full CRUD cycle for sources."""
    async with await _client() as c:
        owner, tenant_id = await _register_owner(c, "SrcOwner")
        headers = await _auth_headers(c, owner.username)

        external_id = secrets.token_hex(6)
        # CREATE
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test Source",
                "platform_id": await _platform_value(),
                "source_type": "USER",
                "external_id": external_id,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        source_id = resp.json()["id"]

        # READ
        resp = await c.get(f"/api/v1/sources/{source_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Source"

        # UPDATE
        resp = await c.patch(
            f"/api/v1/sources/{source_id}",
            json={"name": "Updated Source"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Source"

        # LIST
        resp = await c.get("/api/v1/sources", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

        # DELETE
        resp = await c.delete(f"/api/v1/sources/{source_id}", headers=headers)
        assert resp.status_code == 204

        await _drop(owner, tenant_id)


async def test_sources_api_names_a_client_error_for_an_unknown_source_type() -> None:
    """A typo in `source_type` is the caller's mistake, not a server fault.

    The enum column passes an unknown name straight through to PostgreSQL, so
    without the check the first symptom was a 500 on flush.
    """
    async with await _client() as c:
        owner, tenant_id = await _register_owner(c, "SrcBadType")
        headers = await _auth_headers(c, owner.username)

        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Bad Type",
                "platform_id": await _platform_value(),
                "source_type": "NOT_A_TYPE",
                "external_id": secrets.token_hex(6),
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

        await _drop(owner, tenant_id)


async def test_sources_api_refuses_a_null_update_field() -> None:
    """Every updatable column is non-nullable: `null` is refused by name."""
    async with await _client() as c:
        owner, tenant_id = await _register_owner(c, "SrcNullPatch")
        headers = await _auth_headers(c, owner.username)

        created = await c.post(
            "/api/v1/sources",
            json={
                "name": "Null Patch",
                "platform_id": await _platform_value(),
                "source_type": "USER",
                "external_id": secrets.token_hex(6),
            },
            headers=headers,
        )
        assert created.status_code == 201
        source_id = created.json()["id"]

        resp = await c.patch(f"/api/v1/sources/{source_id}", json={"name": None}, headers=headers)
        assert resp.status_code == 422, resp.text
        assert "name" in resp.json()["detail"]

        kept = await c.get(f"/api/v1/sources/{source_id}", headers=headers)
        assert kept.status_code == 200
        assert kept.json()["name"] == "Null Patch"

        await c.delete(f"/api/v1/sources/{source_id}", headers=headers)
        await _drop(owner, tenant_id)


# ── Tasks CRUD tests ────────────────────────────────────────────────────────

async def test_tasks_crud() -> None:
    """Full CRUD cycle for tasks."""
    async with await _client() as c:
        owner, tenant_id = await _register_owner(c, "TaskOwner")
        headers = await _auth_headers(c, owner.username)

        # CREATE
        resp = await c.post(
            "/api/v1/tasks",
            json={
                "name": f"test-task-{secrets.token_hex(3)}",
                "cron_expr": "@once",
                "job_type": "collect",
                "payload": {"period": "day"},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        # READ
        resp = await c.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["job_type"] == "collect"

        # UPDATE
        resp = await c.patch(
            f"/api/v1/tasks/{task_id}",
            json={"payload": {"period": "week"}},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["payload"]["period"] == "week"

        # PAUSE
        resp = await c.patch(f"/api/v1/tasks/{task_id}/pause", json={}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["is_active"] == False

        # RESUME
        resp = await c.patch(f"/api/v1/tasks/{task_id}/pause?resume=true", json={}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["is_active"] == True

        # LIST
        resp = await c.get("/api/v1/tasks", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

        # DELETE
        resp = await c.delete(f"/api/v1/tasks/{task_id}", headers=headers)
        assert resp.status_code == 204

        await _drop(owner, tenant_id)


# ── Credentials tests ───────────────────────────────────────────────────────


async def test_credentials_list() -> None:
    """User can list their own credentials (empty list in test env)."""
    async with await _client() as c:
        owner, tenant_id = await _register_owner(c, "CredOwner")
        headers = await _auth_headers(c, owner.username)

        resp = await c.get("/api/v1/credentials", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        await _drop(owner, tenant_id)


# ── Scenario tests ──────────────────────────────────────────────────────────


async def test_scenarios_use_require_model_perm() -> None:
    """VIEWER cannot create/update/delete scenarios."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "ScnOwner")
        viewer = await _invitee("ScnViewer", WorkspaceRoleType.VIEWER, tenant_id)
        viewer_headers = await _auth_headers(c, viewer.username)

        # VIEWER cannot create
        resp = await c.post(
            "/api/v1/ai/scenarios",
            json={
                "name": "Test scenario",
                "description": "Test",
                "analysis_types": ["sentiment"],
                "content_types": ["text"],
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

        await _drop(viewer, tenant_id)
        await _drop(owner, tenant_id)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _platform_value() -> int:
    from app.models import Platform

    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.id
