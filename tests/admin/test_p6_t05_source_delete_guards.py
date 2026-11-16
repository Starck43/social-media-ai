"""Real SQLAdmin refusal routes with inert identity/storage; no DB or providers.

The fix disallows raw administrator deletion for every row state, rather than
reimplementing task admission in SQLAdmin. Storage bombs prove denial happens
before reads, writes, or a platform cascade can start. Authentication uses an
inert seam; the application's real authorization backend and views are mounted.
These tests do not claim a deployed PostgreSQL FK or live-cascade reproduction.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqladmin import Admin
from sqladmin.authentication import AuthenticationBackend
from sqladmin.models import ModelView
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request

from app.admin.authorization import AdminAuthorizationBackend
from app.admin.views import PlatformAdmin, SourceAdmin
from app.core.tenant_context import tenant_scope
from app.types import ActionType

__source_only__ = True
pytestmark = pytest.mark.asyncio


@dataclass
class Actor:
    id: int
    is_superuser: bool = False
    is_active: bool = True
    console_access: bool = True
    rights: set = field(default_factory=lambda: {ActionType.VIEW, ActionType.DELETE})

    def has_admin_access(self):
        return self.is_active and self.console_access

    def model_permissions(self, model_name):
        return set(self.rights) if model_name in {"source", "platform"} else set()


ACTORS = {
    "owner": Actor(1, is_superuser=True),
    "delete-operator": Actor(2),
    "view-operator": Actor(3, rights={ActionType.VIEW}),
    "workspace-owner": Actor(4, console_access=False),
    "inactive-operator": Actor(5, is_active=False),
}


class NoStorageSession(AsyncSession):
    def __init__(self, *args, **kwargs):
        raise AssertionError("Raw admin deletion must not open a storage session")


class InertAuthentication(AuthenticationBackend):
    async def authenticate(self, request):
        actor = ACTORS.get(request.headers.get("x-test-actor"))
        if actor is None or not actor.has_admin_access():
            return False
        request.state.admin_user = actor
        return True


@pytest.fixture
def console(monkeypatch):
    app = Starlette()
    backend = AdminAuthorizationBackend()
    admin = Admin(
        app,
        session_maker=async_sessionmaker(class_=NoStorageSession),
        authentication_backend=InertAuthentication(secret_key="p6-t05-inert-session-key-not-production"),
        authorization_backend=backend,
    )
    reads = []
    for cls in (SourceAdmin, PlatformAdmin):
        read = AsyncMock(side_effect=AssertionError("Denied admin deletion must not look up an object"))
        monkeypatch.setattr(cls, "get_object_for_details", read)
        monkeypatch.setattr(cls, "get_object_for_delete", read)
        reads.append(read)
        admin.add_view(cls)
    generic = AsyncMock(side_effect=AssertionError("Generic deletion must never be reached"))
    monkeypatch.setattr(ModelView, "delete_model", generic)
    return SimpleNamespace(app=app, admin=admin, backend=backend, reads=reads, generic=generic)


@pytest.mark.parametrize("cls", [SourceAdmin, PlatformAdmin])
@pytest.mark.parametrize("actor_name", ["owner", "delete-operator", "view-operator", "workspace-owner"])
async def test_direct_view_method_cannot_delete_even_with_owner_or_tenant_scope(console, cls, actor_name):
    actor = ACTORS[actor_name]
    request = Request({"type": "http", "method": "DELETE", "path": "/admin", "headers": [], "session": {}})
    request.state.admin_user = actor
    view = next(v for v in console.admin._views if type(v) is cls)
    with tenant_scope(7):
        with pytest.raises(HTTPException) as refused:
            await view.delete_model(request, "21")
    assert refused.value.status_code == 403
    assert refused.value.detail == (
        "admin_source_delete_disabled" if cls is SourceAdmin else "admin_platform_delete_disabled"
    )
    console.generic.assert_not_awaited()
    for read in console.reads:
        read.assert_not_awaited()


@pytest.mark.parametrize("identity", ["source", "platform"])
@pytest.mark.parametrize("actor_name", ["owner", "delete-operator", "view-operator"])
@pytest.mark.parametrize("pks", ["21", "21,22", "22,21", "999999", "", "21,,22"])
async def test_real_single_bulk_foreign_and_malformed_routes_refuse_before_storage(console, identity, actor_name, pks):
    # IDs may belong to another workspace, a protected last source, an active
    # reader or an unreferenced row: unconditional refusal must not inspect them.
    async with AsyncClient(transport=ASGITransport(app=console.app), base_url="https://test.invalid") as client:
        with tenant_scope(7):
            response = await client.delete(
                f"/admin/{identity}/delete", params={"pks": pks}, headers={"x-test-actor": actor_name}
            )
    assert response.status_code == 403, response.text
    console.generic.assert_not_awaited()
    for read in console.reads:
        read.assert_not_awaited()


@pytest.mark.parametrize("identity", ["source", "platform"])
@pytest.mark.parametrize("actor_name", ["workspace-owner", "inactive-operator", "anonymous"])
async def test_no_workspace_role_or_inactive_identity_grants_console_delete(console, identity, actor_name):
    async with AsyncClient(
        transport=ASGITransport(app=console.app), base_url="https://test.invalid", follow_redirects=False
    ) as client:
        response = await client.delete(
            f"/admin/{identity}/delete", params={"pks": "21,22"}, headers={"x-test-actor": actor_name}
        )
    assert response.status_code == 302 and response.headers["location"].endswith("/admin/login")
    for read in console.reads:
        read.assert_not_awaited()
    console.generic.assert_not_awaited()


@pytest.mark.parametrize("cls", [SourceAdmin, PlatformAdmin])
async def test_ui_and_row_capability_refuse_delete_but_keep_existing_edit_surfaces(console, cls):
    view = next(v for v in console.admin._views if type(v) is cls)
    request = Request({"type": "http", "method": "GET", "path": "/admin", "headers": [], "session": {}})
    request.state.admin_user = ACTORS["owner"]
    await console.backend.load(request)
    assert view.can_delete is False
    assert await view.check_can_delete(request, SimpleNamespace(id=21, tenant_id=7)) is False
    assert view.can_create is True and view.can_edit is True


@pytest.mark.parametrize("active, linked_count", [(True, 1), (True, 2), (False, 1)])
async def test_existing_policy_still_blocks_active_readers_and_paused_last_source(monkeypatch, active, linked_count):
    """Decision regression only; actual DB locks and FK behavior are not validated."""
    from app.models.managers.job_manager import JobManager
    from app.services.collection.source_deletion import SourceDeleteBlocked, delete_source

    source = SimpleNamespace(id=21, tenant_id=7, name="synthetic source", owner_task_id=None)
    task = SimpleNamespace(id=41, tenant_id=7, name="synthetic task", is_active=active)
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: source),
                SimpleNamespace(all=lambda: [(task.id,)]),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [task])),
                SimpleNamespace(all=lambda: [(task.id, linked_count)]),
            ]
        ),
        delete=AsyncMock(side_effect=AssertionError("Protected source must not be deleted")),
        flush=AsyncMock(side_effect=AssertionError("Refusal must not flush writes")),
    )
    lock = AsyncMock()
    monkeypatch.setattr(JobManager, "lock_source_admission", lock)
    result = await delete_source(session, source_id=source.id, tenant_id=7)
    assert isinstance(result, SourceDeleteBlocked)
    assert result.reason == ("source_in_active_task" if active else "source_is_last_task_source")
    assert [ref.task_id for ref in result.blocking_tasks] == [task.id]
    lock.assert_awaited_once_with(session, 7)
    session.delete.assert_not_awaited()
    session.flush.assert_not_awaited()
