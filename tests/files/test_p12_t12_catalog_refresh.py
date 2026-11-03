"""Actual authorized reader + catalog with HTTP MockTransport; no live provider."""

import httpx
import pytest
from sqlalchemy import update

from app.core.database import async_session_maker
from app.integrations.file_workspace.yandex_disk import MetadataError
from app.models import WorkspaceConnection
from app.models.managers.file_catalog_manager import CatalogInputError, FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.catalog_refresh import refresh_catalog_page
from tests.shared.file_catalog_fixtures import actor, data  # noqa: F401

pytestmark = pytest.mark.tenancy
manager = FileCatalogManager()


def response(names=("report.txt",), offset=0):
    return {
        "path": "disk:/work",
        "type": "dir",
        "_embedded": {
            "offset": offset,
            "items": [{"path": "disk:/work/" + name, "name": name, "type": "file"} for name in names],
        },
    }


async def test_reader_to_catalog_replay_and_cursor(data):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.host == "cloud-api.yandex.net"
        return httpx.Response(200, json=response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with actor(data) as args:
            first = await refresh_catalog_page(**args, folder="/work", client=client, limit=1)
            again = await refresh_catalog_page(**args, folder="/work", client=client, limit=1)
            assert first == {"created": 1, "metadata_changed": 0, "next_offset": 1, "truncated": False}
            assert again["created"] == again["metadata_changed"] == 0
            rows = (await manager.list_page(**args))["items"]
            assert len(rows) == 1 and rows[0]["name"] == "report.txt"
            detail = await manager.detail(**args, object_id=rows[0]["id"])
            assert len(detail["observations"]) == 1
    assert len(calls) == 2 and all(call.url.path.endswith("/resources") for call in calls)


@pytest.mark.parametrize("user,tenant", [(1, 0), (0, 1)])
async def test_foreign_actor_never_causes_provider_request(data, user, tenant):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        with actor(data, user=user, tenant=tenant) as args:
            with pytest.raises(ConnectionDenied):
                await refresh_catalog_page(**args, folder="/work", client=client)
    assert calls == []


async def test_policy_changes_between_read_and_admission_deny_storage(data, monkeypatch):
    import app.services.file_workspace.catalog_refresh as bridge

    original = bridge.list_owned_folder

    async def changed(**kwargs):
        page = await original(**kwargs)
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(WorkspaceConnection)
                .where(WorkspaceConnection.id == kwargs["connection_id"])
                .values(is_active=False)
            )
        return page

    monkeypatch.setattr(bridge, "list_owned_folder", changed)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response()))
    ) as client:
        with actor(data) as args:
            with pytest.raises(ConnectionDenied):
                await refresh_catalog_page(**args, folder="/work", client=client)
    from sqlalchemy import func, select

    from app.models import FileCatalogObject

    async with async_session_maker() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(FileCatalogObject)
                .where(FileCatalogObject.connection_id == data.connections[0])
            )
            == 0
        )


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_provider_failure_does_not_persist(data, status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status))) as client:
        with actor(data) as args:
            with pytest.raises(MetadataError):
                await refresh_catalog_page(**args, folder="/work", client=client)
            assert (await manager.list_page(**args))["items"] == []


@pytest.mark.parametrize("extra", [{"client": None}, {"limit": True}, {"offset": -1}, {"limit": 101}])
async def test_no_default_network_and_bounded_arguments(data, extra):
    with actor(data) as args:
        with pytest.raises(CatalogInputError):
            await refresh_catalog_page(**args, folder="/work", **{"client": None, **extra})


async def test_mount_selector_rechecks_root_denial_and_never_projects_secret(data):
    from tests.shared.file_catalog_fixtures import configure

    with actor(data) as args:
        rows = await manager.connections(tenant_id=args["tenant_id"], actor_id=args["actor_id"])
        assert [r["id"] for r in rows] == [data.connections[0]]
        assert all(set(r) == {"id", "label", "root"} for r in rows)
        await configure(data, restrictions=[{"path": "disk:/work", "deny": ["list"]}])
        assert await manager.connections(tenant_id=args["tenant_id"], actor_id=args["actor_id"]) == []
