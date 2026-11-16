"""Real owner/tenant persistence and app middleware for restrictive-only folder edits."""

import re

import httpx
import pytest
from bs4 import BeautifulSoup
from sqlalchemy import delete, select, text, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.hashing import get_password_hash
from app.integrations.file_workspace.policy import FileOperation
from app.main import create_application
from app.models import ActionAuditEvent, TenantUser, User, WorkspaceConnection
from app.models.managers.file_catalog_manager import FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace import folder_permissions as service
from app.services.file_workspace.yandex_reader import _stored_policy
from tests.shared.file_catalog_fixtures import actor, admit, configure, data, page  # noqa: F401

pytestmark = pytest.mark.tenancy
catalog = FileCatalogManager()


@pytest.fixture(autouse=True)
async def purge_audit(data):
    yield
    async with async_session_maker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
        await session.execute(
            delete(ActionAuditEvent).where(ActionAuditEvent.tenant_id.in_([t.id for t in data.tenants]))
        )
    async with async_session_maker() as session:
        assert not (
            await session.scalars(
                select(ActionAuditEvent.id).where(ActionAuditEvent.tenant_id.in_([t.id for t in data.tenants]))
            )
        ).all()


async def edit(args, path, access, revision=None):
    current = await service.get_folder_permissions(**args)
    return await service.set_folder_permissions(
        **args, path=path, access=access, expected_revision=revision or current["revision"]
    )


async def test_inherited_deny_siblings_cached_catalog_and_recovery(data):
    with actor(data) as args:
        await admit(args, "secret.txt", folder="/work/private")
        await admit(args, "visible.txt", folder="/work/public")
        secret = (await catalog.list_page(**args, query="secret"))["items"][0]
        await edit(args, "/work/private", "none")
        await edit(args, "/work/private/child", "read_only")
        child = await service.get_folder_permissions(**args, path="/work/private/child")
        assert not child["list_allowed"] and not child["read_allowed"]
        assert child["denied_by"] == {"list": ["disk:/work/private"], "read": ["disk:/work/private"]}
        assert [r["name"] for r in (await catalog.list_page(**args))["items"]] == ["visible.txt"]
        assert (await catalog.list_page(**args, query="secret"))["items"] == []
        with pytest.raises(ConnectionDenied):
            await catalog.detail(**args, object_id=secret["id"])
        await edit(args, "/work/private", "read_only")
        assert (await catalog.detail(**args, object_id=secret["id"]))["name"] == "secret.txt"


async def test_revoke_then_restore_never_revives_queued_ticket_or_binding(data):
    with actor(data) as args:
        ticket = await catalog.begin_page(**args, folder="/work")
        async with async_session_maker() as session:
            before = _stored_policy((await session.get(WorkspaceConnection, args["connection_id"])).config).binding()
        await edit(args, "/work", "none")
        assert (await service.get_folder_permissions(**args))["read_allowed"] is False
        await edit(args, "/work", "read_only")
        with pytest.raises(ConnectionDenied):
            await catalog.admit_page(**args, ticket=ticket, page=page("late.txt"))
        async with async_session_maker() as session:
            policy = _stored_policy((await session.get(WorkspaceConnection, args["connection_id"])).config)
        assert policy.binding() != before
        for operation in (FileOperation.CREATE, FileOperation.MODIFY, FileOperation.DELETE):
            assert not policy.permits("/work/file", operation)


async def test_conflict_and_transactional_audit_failure(data, monkeypatch):
    with actor(data) as args:
        initial = await service.get_folder_permissions(**args)
        changed = await edit(args, "/work/private", "none")
        with pytest.raises(service.FolderPolicyConflict):
            await edit(args, "/work", "none", initial["revision"])
        async with async_session_maker() as session:
            events = (
                await session.scalars(select(ActionAuditEvent).where(ActionAuditEvent.tenant_id == args["tenant_id"]))
            ).all()
        assert len(events) == 1
        assert (events[0].actor_id, events[0].resource_id, events[0].action) == (
            str(args["actor_id"]),
            str(args["connection_id"]),
            "files.policy_update",
        )

        async def fail(**kwargs):
            raise RuntimeError("synthetic audit failure")

        monkeypatch.setattr(ActionAuditEvent.objects, "record_success", fail)
        with pytest.raises(RuntimeError):
            await edit(args, "/work", "none")
        assert (await service.get_folder_permissions(**args))["revision"] == changed["revision"]


@pytest.mark.parametrize("user,tenant", [(1, 0), (0, 1), (1, 1)])
async def test_foreign_actor_tenant_and_owner_denied(data, user, tenant):
    with actor(data) as args:
        revision = (await service.get_folder_permissions(**args))["revision"]
    with actor(data, user=user, tenant=tenant) as args:
        with pytest.raises(ConnectionDenied):
            await service.get_folder_permissions(**args)
        with pytest.raises(ConnectionDenied):
            await service.set_folder_permissions(**args, path="/work", access="none", expected_revision=revision)


@pytest.mark.parametrize(
    "config",
    [
        {"file_policy": None},
        {},
        {"file_policy": {}},
        {"file_policy": {"version": 1, "root": "/work", "restrictions": [], "epoch": "bad"}},
    ],
)
async def test_missing_corrupt_unknown_policy_denied(data, config):
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(WorkspaceConnection).where(WorkspaceConnection.id == data.connections[0]).values(config=config)
        )
    with actor(data) as args:
        with pytest.raises(ConnectionDenied):
            await service.get_folder_permissions(**args)


async def test_validation_and_membership_revocation(data):
    with actor(data) as args:
        for path, access in (("/outside", "none"), ("/work/../private", "none"), ("/work", "delete")):
            with pytest.raises(service.FolderPolicyInputError):
                await edit(args, path, access)
        revision = (await service.get_folder_permissions(**args))["revision"]
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser)
                .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                .values(is_active=False)
            )
        with pytest.raises(ConnectionDenied):
            await service.set_folder_permissions(**args, path="/work", access="none", expected_revision=revision)


@pytest.fixture
async def web(data, monkeypatch):
    monkeypatch.setattr(settings, "FILE_CATALOG_UI_ENABLED", True)
    password = "folder-policy-synthetic-password"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id.in_([u.id for u in data.users]))
            .values(hashed_password=get_password_hash(password))
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_application()), base_url="https://testserver", follow_redirects=False
    ) as client:

        async def login(index):
            client.cookies.clear()
            response = await client.get("/app/login")
            csrf = re.search(r'name="_csrf" value="([^\"]+)"', response.text).group(1)
            response = await client.post(
                "/app/login", data={"username": data.users[index].username, "password": password, "_csrf": csrf}
            )
            assert response.status_code in (302, 303)

        await login(0)
        yield client, login


def form_values(response):
    html = BeautifulSoup(response.text, "html.parser")
    form = html.select_one('form[method="post"]')
    return {item["name"]: item.get("value", "") for item in form.select("input[name]")}


async def test_authenticated_editor_csrf_stale_form_and_foreign_owner(data, web, monkeypatch):
    client, login = web
    url = f"/app/knowledge/folders/{data.connections[0]}/permissions"
    response = await client.get(url)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "Создание, изменение и удаление файлов выключены" in response.text
    assert "p12t12-synthetic-token" not in response.text
    values = {**form_values(response), "access": "none"}
    assert (await client.post(url, data={**values, "_csrf": "bad"})).status_code == 403
    assert (await client.post(url, data={**values, "delete": "1"})).status_code == 403
    assert (await client.post(url, data=values)).status_code == 303
    assert (await client.post(url, data=values)).status_code == 409
    denied = await client.get(url)
    assert denied.status_code == 200 and "запрещён" in denied.text
    # A root denial must not prevent the owner from recovering through settings.
    restore = {**form_values(denied), "access": "read_only"}
    assert (await client.post(url, data=restore)).status_code == 303
    await login(1)
    assert (await client.get(url)).status_code == 404
    assert (await client.post(url, data=restore)).status_code == 403
    monkeypatch.setattr(settings, "FILE_CATALOG_UI_ENABLED", False)
    assert (await client.get(url)).status_code == 404
    assert (await client.post(url, data=restore)).status_code == 404
