"""Owner-local acceptance (P6-T05): raw admin deletion is refused on the real test schema.

Never run remotely or with ``--noconftest``. The root conftest redirects to
``test_schema`` before application imports; this file only validates that
redirect. The real application is mounted with production authentication,
session, middleware and storage (loopback PostgreSQL only). Each test creates
its own synthetic tenant/platform/source and restores everything: the admin
delete path must refuse before it can delete a row or start a platform
cascade, and the synthetic rows survive every refused attempt.
"""

from __future__ import annotations

import re
import socket
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

# isort: split
# Guard BEFORE application imports/engine construction, including collection.
_expected_conftest = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected_conftest)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: current root conftest and test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in ("localhost", "127.0.0.1")
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the owner-approved local social_manager/test_schema target is allowed")
# isort: split

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope, service_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Platform, PlatformRole, Source, Tenant, User
from app.types import PlatformType, SourceType
from tests.shared.platform_catalog import shared_platform

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
PASSWORD = "Synthetic-Admin-Delete-K3!"
CSRF = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


@contextmanager
def platform_scope():
    """Trusted fixture setup/readback only; never wrap an ASGI request."""
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


def csrf_token(html: str) -> str:
    match = CSRF.search(html)
    assert match, "The rendered SQLAdmin form must carry its CSRF token"
    return match.group(1)


async def admin_login(client: httpx.AsyncClient, user: User) -> None:
    page = await client.get("/admin/login")
    assert page.status_code == 200, page.text
    response = await client.post(
        "/admin/login",
        data={"username": user.username, "password": PASSWORD, "csrf_token": csrf_token(page.text)},
    )
    assert response.status_code in {302, 303}, f"Real admin login failed: {response.status_code}"


@pytest.fixture
def admin_app(monkeypatch):
    """Real app, real session/auth and only loopback PostgreSQL sockets."""
    monkeypatch.setattr(settings, "ADMIN_ENABLED", True)
    forbidden: list[str] = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def network_guard(original):
        def connect(sock, address):
            if sock.family in {socket.AF_INET, socket.AF_INET6}:
                if address[0] not in {"localhost", "127.0.0.1", "::1"} or address[1] != 5432:
                    forbidden.append(f"{address[0]}:{address[1]}")
                    raise AssertionError("Only approved loopback PostgreSQL TCP is permitted")
            return original(sock, address)

        return connect

    monkeypatch.setattr(socket.socket, "connect", network_guard(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", network_guard(original_connect_ex))
    yield create_application()
    assert not forbidden, "Forbidden network attempted: " + ", ".join(forbidden)


@pytest.fixture
async def user_factory():
    """Owned users with unique names; deleted even when an assertion fails."""
    created: list[int] = []
    marker = f"admin-del-{uuid4().hex[:12]}"

    async def _make(role_code: str) -> User:
        with platform_scope():
            role = await PlatformRole.objects.get_by_codename(role_code)
            assert role is not None, f"Seeded platform role {role_code!r} is required; never seed here"
            username = f"{marker}-{len(created)}"
            user = await User.objects.create_user(
                username=username,
                email=f"{username}@example.invalid",
                password=PASSWORD,
                role_id=role.id,
                is_active=True,
            )
        created.append(user.id)
        return user

    yield NS(make=_make)
    with platform_scope():
        for user_id in created:
            try:
                await User.objects.delete_user(user_id)
            except HTTPException:
                # The owner-protected role cannot be deleted outright; demote
                # it the way the console would, then remove the account.
                plain = await PlatformRole.objects.get_by_codename("user")
                await User.objects.update_by_id(user_id, role_id=plain.id)
                await User.objects.delete_user(user_id)


@pytest.fixture
async def owned_workspace():
    """One synthetic tenant with a platform and a source, always restored."""
    marker = f"p6t05-{uuid4().hex[:10]}"
    tenant = None
    platform = None
    try:
        with platform_scope():
            tenant = await Tenant.objects.create(
                name=f"Admin delete {marker}", slug=f"admin-del-{marker}", plan="business"
            )
            platform = await shared_platform(PlatformType.TELEGRAM.db_value)
        with tenant_scope(tenant.id), service_permission_scope("source", "create"):
            source = await Source.objects.create(
                platform_id=platform.id,
                name=f"{marker}-source",
                external_id=f"p6t05-{marker}",
                source_type=SourceType.GROUP,
            )
        yield NS(tenant_id=tenant.id, platform_id=platform.id, source_id=source.id)
    finally:
        with platform_scope():
            await Tenant.objects.delete_by_id(tenant.id)
            # The platform row is shared catalog reference data, not owned by
            # this test, so it is never deleted here.


async def _source_rows(platform_id: int, tenant_id: int) -> int:
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.count())
            .select_from(Source)
            .where(
                Source.platform_id == platform_id,
                Source.tenant_id == tenant_id,
            )
        )


async def _csrf(app) -> str:
    return app.state.csrf_manager.generate_token()


async def test_real_admin_cannot_delete_a_source_over_asgi(admin_app, user_factory, owned_workspace):
    """A real console login plus the DELETE route refuses before storage."""
    user = await user_factory.make("admin")
    headers = {"X-CSRF-Token": await _csrf(admin_app)}
    async with admin_client(admin_app) as client:
        await admin_login(client, user)
        response = await client.delete(
            "/admin/source/delete", params={"pks": str(owned_workspace.source_id)}, headers=headers
        )
    assert response.status_code == 403, response.text

    with tenant_scope(owned_workspace.tenant_id):
        assert await Source.objects.get(id=owned_workspace.source_id) is not None
    assert await _source_rows(owned_workspace.platform_id, owned_workspace.tenant_id) == 1


async def test_real_admin_platform_delete_refuses_before_its_source_cascade(admin_app, user_factory, owned_workspace):
    """A platform delete would cascade its source; the route refuses first."""
    user = await user_factory.make("superuser")
    headers = {"X-CSRF-Token": await _csrf(admin_app)}
    async with admin_client(admin_app) as client:
        await admin_login(client, user)
        response = await client.delete(
            "/admin/platform/delete", params={"pks": str(owned_workspace.platform_id)}, headers=headers
        )
    assert response.status_code == 403, response.text

    assert await _source_rows(owned_workspace.platform_id, owned_workspace.tenant_id) == 1
    with platform_scope():
        assert await Platform.objects.get(id=owned_workspace.platform_id) is not None
    with tenant_scope(owned_workspace.tenant_id):
        assert await Source.objects.get(id=owned_workspace.source_id) is not None


async def test_real_admin_bulk_and_malformed_pks_leave_storage_untouched(admin_app, user_factory, owned_workspace):
    """Bulk or malformed ids cannot start a deletion for any role."""
    user = await user_factory.make("operator")
    headers = {"X-CSRF-Token": await _csrf(admin_app)}
    async with admin_client(admin_app) as client:
        await admin_login(client, user)
        for pks in (
            f"{owned_workspace.source_id},{owned_workspace.source_id + 1}",
            f"{owned_workspace.source_id},,999999",
            "",
        ):
            response = await client.delete("/admin/source/delete", params={"pks": pks}, headers=headers)
            assert response.status_code == 403, f"pks={pks!r} -> {response.status_code}: {response.text}"

    with tenant_scope(owned_workspace.tenant_id):
        assert await Source.objects.get(id=owned_workspace.source_id) is not None
