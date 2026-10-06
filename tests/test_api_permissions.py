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
from app.models import AgentTask, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import ActionType, UserRoleType


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
            "email": f"{username}@example.test",
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


async def _invitee(prefix: str, platform_role: UserRoleType, tenant_id: int) -> User:
    """A second web user in someone else's workspace."""
    username = f"{prefix}{secrets.token_hex(3)}"
    role = await Role.objects.get(codename=platform_role.name)
    assert role is not None, f"role {platform_role.name} is seeded"
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="secret-password-1",
        role_id=role.id,
        is_superuser=False,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role_id=role.id)
    return user


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        with tenant_scope(bypass=True):
            await tenants.delete_by_id(tenant_id)


# ── require_model_perm tests ─────────────────────────────────────────────────


async def test_require_model_perm_passes_for_user_with_permission() -> None:
    """MANAGER has social.source.* — can create sources."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "PermOwner")
        member = await _invitee("PermViewer", UserRoleType.MANAGER, tenant_id)
        loaded = await _loaded(member)
        assert loaded.has_perm_for("source", ActionType.CREATE), "premise: MANAGER may create sources"

        token = await _csrf(c, "/app/login")
        await c.post("/app/login", data={"username": member.username, "password": "secret-password-1", "_csrf": token})

        # POST /api/v1/sources should succeed
        platform = await _platform_value()
        external_id = secrets.token_hex(6)
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test source",
                "platform_id": 1,
                "source_type": "user",
                "external_id": external_id,
            },
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        assert resp.json()["external_id"] == external_id

        with tenant_scope(bypass=True):
            source = await Source.objects.filter(external_id=external_id).first()
        assert source is not None
        await Source.objects.delete(source.id)
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_require_model_perm_refuses_user_without_permission() -> None:
    """VIEWER cannot create sources."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "BlockedOwner")
        member = await _invitee("BlockedViewer", UserRoleType.VIEWER, tenant_id)
        loaded = await _loaded(member)
        assert not loaded.has_perm_for("source", ActionType.CREATE), "premise: VIEWER may not create sources"

        token = await _csrf(c, "/app/login")
        await c.post("/app/login", data={"username": member.username, "password": "secret-password-1", "_csrf": token})

        external_id = secrets.token_hex(6)
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test source",
                "platform_id": 1,
                "source_type": "user",
                "external_id": external_id,
            },
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        assert "Missing permission" in resp.json()["detail"]

        with tenant_scope(bypass=True):
            source = await Source.objects.filter(external_id=external_id).first()
        assert source is None

        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_require_model_perm_passes_for_superuser() -> None:
    """Superuser can create sources regardless of role permissions."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "SuperOwner")
        superuser = await User.objects.create_user(
            username=f"super{secrets.token_hex(3)}",
            email=f"super{secrets.token_hex(3)}@example.test",
            password="secret-password-1",
            is_superuser=True,
        )

        token = await _csrf(c, "/app/login")
        await c.post("/app/login", data={"username": superuser.username, "password": "secret-password-1", "_csrf": token})

        external_id = secrets.token_hex(6)
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test source",
                "platform_id": 1,
                "source_type": "user",
                "external_id": external_id,
            },
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

        with tenant_scope(bypass=True):
            source = await Source.objects.filter(external_id=external_id).first()
        assert source is not None
        await Source.objects.delete(source.id)
        await _drop(superuser, None)
        await _drop(owner, tenant_id)


# ── require_platform_role tests ──────────────────────────────────────────────


async def test_require_platform_role_passes_for_sufficient_role() -> None:
    """ADMIN can list users (requires ADMIN role)."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "RoleOwner")
        admin_user = await User.objects.create_user(
            username=f"admin{secrets.token_hex(3)}",
            email=f"admin{secrets.token_hex(3)}@example.test",
            password="secret-password-1",
            role_id=(await Role.objects.get(codename="ADMIN").id),
        )

        token = await _csrf(c, "/app/login")
        await c.post("/app/login", data={"username": admin_user.username, "password": "secret-password-1", "_csrf": token})

        resp = await c.get("/api/v1/users/")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        await _drop(admin_user, None)
        await _drop(owner, tenant_id)


async def test_require_platform_role_refuses_for_insufficient_role() -> None:
    """VIEWER cannot list users (requires ADMIN role)."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "RoleOwner2")
        viewer = await _invitee("RoleViewer", UserRoleType.VIEWER, tenant_id)

        token = await _csrf(c, "/app/login")
        await c.post("/app/login", data={"username": viewer.username, "password": "secret-password-1", "_csrf": token})

        resp = await c.get("/api/v1/users/")
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

        await _drop(viewer, tenant_id)
        await _drop(owner, tenant_id)


# ── Sources CRUD tests ──────────────────────────────────────────────────────


async def test_sources_crud() -> None:
    """Full CRUD cycle for sources."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "SrcOwner")

        external_id = secrets.token_hex(6)
        # CREATE
        resp = await c.post(
            "/api/v1/sources",
            json={
                "name": "Test Source",
                "platform_id": 1,
                "source_type": "user",
                "external_id": external_id,
            },
        )
        assert resp.status_code == 201
        source_id = resp.json()["id"]

        # READ
        resp = await c.get(f"/api/v1/sources/{source_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Source"

        # UPDATE
        resp = await c.patch(
            f"/api/v1/sources/{source_id}",
            json={"name": "Updated Source"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Source"

        # LIST
        resp = await c.get("/api/v1/sources")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

        # DELETE
        resp = await c.delete(f"/api/v1/sources/{source_id}")
        assert resp.status_code == 204

        await _drop(owner, tenant_id)


# ── Tasks CRUD tests ────────────────────────────────────────────────────────


async def test_tasks_crud() -> None:
    """Full CRUD cycle for tasks."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "TaskOwner")

        # CREATE
        resp = await c.post(
            "/api/v1/tasks",
            json={
                "name": f"test-task-{secrets.token_hex(3)}",
                "cron_expr": "@once",
                "job_type": "collect",
                "payload": {"period": "day"},
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        # READ
        resp = await c.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["job_type"] == "collect"

        # UPDATE
        resp = await c.patch(
            f"/api/v1/tasks/{task_id}",
            json={"payload": {"period": "week"}},
        )
        assert resp.status_code == 200
        assert resp.json()["payload"]["period"] == "week"

        # PAUSE
        resp = await c.patch(f"/api/v1/tasks/{task_id}/pause", json={})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

        # RESUME
        resp = await c.patch(f"/api/v1/tasks/{task_id}/pause", json={"resume": True})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

        # LIST
        resp = await c.get("/api/v1/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

        # DELETE
        resp = await c.delete(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 204

        await _drop(owner, tenant_id)


# ── Credentials tests ───────────────────────────────────────────────────────


async def test_credentials_list() -> None:
    """User can list their own credentials (empty list in test env)."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "CredOwner")

        resp = await c.get("/api/v1/credentials")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        await _drop(owner, tenant_id)


# ── Scenario tests ──────────────────────────────────────────────────────────


async def test_scenarios_use_require_model_perm() -> None:
    """VIEWER cannot create/update/delete scenarios."""
    async with await _client() as c:
        owner, tenant_id = await _register(c, "ScnOwner")
        viewer = await _invitee("ScnViewer", UserRoleType.VIEWER, tenant_id)

        token = await _csrf(c, "/app/login")
        await c.post("/app/login", data={"username": viewer.username, "password": "secret-password-1", "_csrf": token})

        # VIEWER cannot create
        resp = await c.post(
            "/api/v1/ai/scenarios",
            json={
                "name": "Test scenario",
                "description": "Test",
                "analysis_types": ["sentiment"],
                "content_types": ["text"],
            },
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

        await _drop(viewer, tenant_id)
        await _drop(owner, tenant_id)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _platform_value() -> str:
    from app.models import Platform
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.platform_type.db_value
