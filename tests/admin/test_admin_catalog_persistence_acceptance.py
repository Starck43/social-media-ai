"""Owner-local acceptance: persisted SQLAdmin catalog edits on the real test schema.

Never run remotely or with ``--noconftest``. The root conftest redirects to
``test_schema`` before application imports; this file only validates that
redirect and reuses the existing seeded roles/permissions. It never resets,
seeds or migrates the schema, and every edited canonical row/grant is restored.
"""

from __future__ import annotations

import json
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
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import LLMModel, LLMProvider, Permission, PlatformRole, User
from app.types import ActionType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
PASSWORD = "Synthetic-Catalog-Q7!"
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
async def make_user():
    """Owned users with unique names; deleted even when an assertion fails."""
    created: list[int] = []
    marker = f"catalog-acc-{uuid4().hex[:12]}"

    async def _make(role_code: str, *, active: bool = True) -> User:
        with platform_scope():
            role = await PlatformRole.objects.get_by_codename(role_code)
            assert role is not None, f"Seeded platform role {role_code!r} is required; never seed here"
            username = f"{marker}-{len(created)}"
            user = await User.objects.create_user(
                username=username,
                email=f"{username}@example.invalid",
                password=PASSWORD,
                role_id=role.id,
                is_active=active,
            )
        created.append(user.id)
        return user

    yield _make
    with platform_scope():
        for user_id in created:
            await User.objects.delete_user(user_id)


@pytest.fixture
async def catalog_snapshot():
    """Snapshot the canonical operator grants and one permission label, restore after."""
    with platform_scope():
        operator = await PlatformRole.objects.get_by_codename("operator")
        role = await PlatformRole.objects.get_with_permissions(operator.id)
        original_grants = sorted({permission.id for permission in role.permissions})
        label = await Permission.objects.get_by_codename("identity.model_type.view")
        grant = await Permission.objects.get_by_codename("identity.permission.view")
        assert label is not None and grant is not None, "The seeded access catalog must provide these permissions"
        snapshot = NS(
            operator_id=operator.id,
            original_grants=original_grants,
            label_id=label.id,
            label_codename=label.codename,
            label_model_type_id=label.model_type_id,
            original_label=label.name,
            grant_ids=sorted({label.id, grant.id}),
        )
    yield snapshot
    with platform_scope():
        await PlatformRole.objects.set_permissions(snapshot.operator_id, snapshot.original_grants)
        await Permission.objects.update_by_id(snapshot.label_id, name=snapshot.original_label)


@pytest.fixture
async def owned_model(make_user, record_property):
    """A credential-free local model; mock mode never dispatches provider traffic."""
    marker = f"catalog-model-{uuid4().hex[:12]}"
    owner = None
    provider_id = model_id = None
    try:
        owner = await make_user("admin")
        with platform_scope():
            provider = await LLMProvider.objects.create(
                owner_user_id=owner.id,
                uid=marker,
                name=marker,
                base_url="https://provider.invalid/v1",
                api_format="openai",
                is_active=True,
            )
            provider_id = provider.id
            assert provider.encrypted_api_key is None, "Mock acceptance must not need a provider key"
            model = await LLMModel.objects.create(
                provider_id=provider.id,
                owner_user_id=owner.id,
                name=marker,
                model_id=marker,
                model_type="text",
                max_tokens=64,
                default_temperature=0.0,
                input_cost_per_1k=0.0,
                output_cost_per_1k=0.0,
                is_active=True,
                is_default=False,
            )
            model_id = model.id
        yield NS(owner=owner, owner_user_id=owner.id, provider_id=provider_id, model_id=model_id)
    finally:
        record_property(
            "owned_catalog_fixture_manifest",
            json.dumps(
                {
                    "marker": marker,
                    "owner_user_id": getattr(owner, "id", None),
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "retained": False,
                },
                sort_keys=True,
            ),
        )
        with platform_scope():
            if model_id is not None:
                await LLMModel.objects.delete_by_id(model_id)
            if provider_id is not None:
                await LLMProvider.objects.delete_by_id(provider_id)


async def stored_grants(role_id: int) -> set[int]:
    with platform_scope():
        role = await PlatformRole.objects.get_with_permissions(role_id)
        return {permission.id for permission in role.permissions}


async def stored_label(permission_id: int) -> str:
    with platform_scope():
        permission = await Permission.objects.get(id=permission_id)
        return permission.name


async def test_platform_admin_persists_role_grants_and_permission_label(admin_app, make_user, catalog_snapshot):
    """Actual SQLAdmin forms write through the canonical managers and keep identities."""
    admin = await make_user("admin")
    async with admin_client(admin_app) as client:
        await admin_login(client, admin)
        page = await client.get(f"/admin/platform-role/edit/{catalog_snapshot.operator_id}")
        assert page.status_code == 200, page.text
        assert 'name="permissions"' in page.text
        assert 'name="codename"' not in page.text and 'name="users"' not in page.text
        response = await client.post(
            f"/admin/platform-role/edit/{catalog_snapshot.operator_id}",
            data={
                "permissions": [str(catalog_snapshot.grant_ids[0]), str(catalog_snapshot.grant_ids[1])],
                "save": "Save",
                "csrf_token": csrf_token(page.text),
            },
        )
        assert response.status_code in {302, 303}, response.text

        with platform_scope():
            stored_role = await PlatformRole.objects.get_with_permissions(catalog_snapshot.operator_id)
        assert stored_role.codename == "operator"
        assert {permission.id for permission in stored_role.permissions} == set(catalog_snapshot.grant_ids)

        label = f"Acceptance catalog label {uuid4().hex[:12]}"
        page = await client.get(f"/admin/permission/edit/{catalog_snapshot.label_id}")
        assert page.status_code == 200, page.text
        assert 'name="name"' in page.text
        assert 'name="action_type"' not in page.text and 'name="model_type_id"' not in page.text
        response = await client.post(
            f"/admin/permission/edit/{catalog_snapshot.label_id}",
            data={"name": f"  {label}  ", "save": "Save", "csrf_token": csrf_token(page.text)},
        )
        assert response.status_code in {302, 303}, response.text

        with platform_scope():
            stored_permission = await Permission.objects.prefetch_related("model_type").get(
                id=catalog_snapshot.label_id
            )
        assert stored_permission.name == label
        assert stored_permission.codename == catalog_snapshot.label_codename == "identity.model_type.view"
        assert stored_permission.model_type.resource_key == "identity.model_type"
        assert stored_permission.model_type_id == catalog_snapshot.label_model_type_id
        assert stored_permission.action_type == ActionType.VIEW

        page = await client.get(f"/admin/platform-role/edit/{catalog_snapshot.operator_id}")
        assert page.status_code == 200, page.text
        assert f"identity.model_type.view — {label}" in page.text


@pytest.mark.parametrize("case", ["operator", "revoked", "inactive", "anonymous", "csrf_missing", "csrf_invalid"])
async def test_denied_catalog_writes_leave_storage_unchanged(admin_app, make_user, catalog_snapshot, case):
    """Operator, stale identity, anonymous and CSRF failures never reach storage."""
    async with admin_client(admin_app) as client:
        if case == "operator":
            user = await make_user("operator")
            await admin_login(client, user)
        elif case in {"revoked", "inactive", "csrf_missing", "csrf_invalid"}:
            user = await make_user("admin")
            await admin_login(client, user)
            if case == "revoked":
                with platform_scope():
                    user_role = await PlatformRole.objects.get_by_codename("user")
                    await User.objects.update_by_id(user.id, role_id=user_role.id)
            elif case == "inactive":
                with platform_scope():
                    await User.objects.update_by_id(user.id, is_active=False)

        token = admin_app.state.csrf_manager.generate_token()
        if case == "csrf_missing":
            token = ""
        elif case == "csrf_invalid":
            token = token + "invalid"

        role_response = await client.post(
            f"/admin/platform-role/edit/{catalog_snapshot.operator_id}",
            data={
                "permissions": [str(catalog_snapshot.grant_ids[0])],
                "save": "Save",
                "csrf_token": token,
            },
        )
        permission_response = await client.post(
            f"/admin/permission/edit/{catalog_snapshot.label_id}",
            data={"name": "Mutated acceptance label", "save": "Save", "csrf_token": token},
        )

    if case in {"csrf_missing", "csrf_invalid"}:
        assert role_response.status_code == 400, role_response.text
        assert permission_response.status_code == 400, permission_response.text
    else:
        for response in (role_response, permission_response):
            assert response.status_code in {302, 303, 403}, response.text

    assert await stored_grants(catalog_snapshot.operator_id) == set(catalog_snapshot.original_grants)
    assert await stored_label(catalog_snapshot.label_id) == catalog_snapshot.original_label


async def test_mock_model_post_keeps_shared_menu_and_verified_actor(admin_app, owned_model, monkeypatch):
    """Real session/auth renders the shared shell; local mock needs no provider call."""
    from app.admin import actions

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Mock mode must not call a provider or connectivity probe")

    monkeypatch.setattr(actions, "probe_connectivity", forbidden)
    monkeypatch.setattr(actions, "probe_real_capped", forbidden)
    admin = owned_model.owner
    async with admin_client(admin_app) as client:
        await admin_login(client, admin)
        action_page = await client.get("/admin/llm-model/action/test-model", params={"pks": owned_model.model_id})
        assert action_page.status_code == 200, action_page.text
        response = await client.post(
            "/admin/llm-model/test",
            data={
                "model_id": str(owned_model.model_id),
                "test_type": "mock",
                "prompt": "Локальная проверка без провайдера",
                "save": "Save",
                "csrf_token": csrf_token(action_page.text),
            },
        )
    assert response.status_code == 200, response.text
    assert "[МОК-ПРОБА]" in response.text and "Провайдер не вызывался." in response.text
    assert f"<strong data-admin-username>{admin.username}</strong>" in response.text
    assert "<span data-admin-role>Администратор платформы</span>" in response.text
    assert "data-sqladmin-menu" in response.text and "/admin/user/list" in response.text
