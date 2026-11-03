"""Synthetic one-folder scan, offset retries and privacy; never a live provider."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import update

from app.core.database import async_session_maker
from app.integrations.file_workspace.yandex_disk import DiskItem, DiskPage
from app.models import FileCatalogScan, WorkspaceConnection
from app.models.managers import file_catalog_manager as catalog_module
from app.models.managers.file_catalog_manager import CatalogScanChanged, CatalogScanClaim, FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace import catalog_scan as scan
from tests.shared.file_catalog_fixtures import actor, admit, configure, data  # noqa: F401

pytestmark = pytest.mark.tenancy
manager = FileCatalogManager()


def provider_page(offset, names):
    return {
        "path": "disk:/work",
        "type": "dir",
        "_embedded": {
            "offset": offset,
            "items": [{"path": f"disk:/work/{n}", "name": n, "type": "file"} for n in names],
        },
    }


async def test_pagination_retries_same_offset_and_keeps_old_catalog_rows(data):
    calls, fail = [], [True]
    names = ["one.txt", "two.txt", "three.txt", "four.txt", "five.txt"]

    def handle(request):
        offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
        calls.append(offset)
        if offset == 2 and fail[0]:
            fail[0] = False
            return httpx.Response(503, text="synthetic secret must not surface")
        return httpx.Response(200, json=provider_page(offset, names[offset : offset + limit]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with actor(data) as args:
            await admit(args, "previously-saved.txt")
            assert (await scan.start_catalog_scan(**args, folder="/work", page_size=2))["next_offset"] == 0
            assert (await scan.resume_catalog_scan(**args, client=client)) == {
                "state": "partial",
                "error_code": None,
                "next_offset": 2,
            }
            failed = await scan.resume_catalog_scan(**args, client=client)
            assert failed == {"state": "error", "error_code": "provider_unavailable", "next_offset": 2}
            assert await scan.catalog_scan_status(**args) == failed
            assert (await scan.resume_catalog_scan(**args, client=client))["next_offset"] == 4
            assert (await scan.resume_catalog_scan(**args, client=client))["state"] == "exhausted"
            with pytest.raises(scan.ScanNotResumable):
                await scan.resume_catalog_scan(**args, client=client)
            rows = (await manager.list_page(**args))["items"]
            assert {row["name"] for row in rows} == set(names + ["previously-saved.txt"])
            assert all(row["observation_number"] == 1 for row in rows)
    assert calls == [0, 2, 2, 4]


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(401), "reauthorization_required"),
        (httpx.Response(429), "rate_limited"),
        (httpx.Response(503), "provider_unavailable"),
        (httpx.Response(200, json=provider_page(7, ["private.txt"])), "invalid_provider_response"),
    ],
)
async def test_failures_are_sanitized_and_do_not_advance_or_admit(data, response, code):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder="/work", page_size=2)
            result = await scan.resume_catalog_scan(**args, client=client)
            assert result == {"state": "error", "error_code": code, "next_offset": 0}
            assert (await manager.list_page(**args))["items"] == []


async def test_busy_expired_claim_and_replaced_scan_never_admit_stale_page(data):
    with actor(data) as args:
        await scan.start_catalog_scan(**args, folder="/work", page_size=1)
        scan_token, lease_token, folder, offset, limit = await scan._claim(**args)
        ticket = await manager.begin_page(**args, folder=folder)
        with pytest.raises(scan.ScanBusy):
            await scan.start_catalog_scan(**args, folder="/work")
        assert (await scan.catalog_scan_status(**args))["state"] == "running"
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(FileCatalogScan)
                .where(FileCatalogScan.connection_id == args["connection_id"])
                .values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
        await scan.start_catalog_scan(**args, folder="/work", page_size=1)
        with pytest.raises(CatalogScanChanged):
            await manager.admit_page(
                **args,
                ticket=ticket,
                page=DiskPage((DiskItem("disk:/work/stale.txt", "stale.txt", "file"),), 1),
                _scan_claim=CatalogScanClaim(scan_token, lease_token, offset, limit),
            )
        assert (await manager.list_page(**args))["items"] == []
        assert (await scan.catalog_scan_status(**args))["next_offset"] == 0


async def test_policy_change_and_foreign_owner_close_scan(data):
    with actor(data) as args:
        await scan.start_catalog_scan(**args, folder="/work")
        await configure(data, restrictions=[{"path": "disk:/work/private", "deny": ["list"]}])
        assert (await scan.catalog_scan_status(**args))["state"] == "policy_changed"
        with pytest.raises(scan.ScanChanged):
            await scan._claim(**args)
    with actor(data, user=1) as foreign:
        with pytest.raises(ConnectionDenied):
            await scan.catalog_scan_status(**foreign)


async def test_revocation_after_read_prevents_admission(data, monkeypatch):
    original = scan.list_owned_folder

    async def revoke(**kwargs):
        page = await original(**kwargs)
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(WorkspaceConnection)
                .where(WorkspaceConnection.id == kwargs["connection_id"])
                .values(is_active=False)
            )
        return page

    monkeypatch.setattr(scan, "list_owned_folder", revoke)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=provider_page(0, ["x.txt"])))
    ) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder="/work")
            with pytest.raises(ConnectionDenied):
                await scan.resume_catalog_scan(**args, client=client)
    async with async_session_maker() as session:
        from sqlalchemy import func, select

        from app.models import FileCatalogObject

        assert (
            await session.scalar(
                select(func.count())
                .select_from(FileCatalogObject)
                .where(FileCatalogObject.connection_id == data.connections[0])
            )
            == 0
        )


async def test_capacity_and_invalid_cursor_do_not_advance(data, monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=provider_page(0, ["x.txt"])))
    ) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder="/work", page_size=1)
            monkeypatch.setattr(catalog_module, "MAX_OBJECTS", 0)
            assert (await scan.resume_catalog_scan(**args, client=client)) == {
                "state": "error",
                "error_code": "catalog_quota",
                "next_offset": 0,
            }
            monkeypatch.setattr(catalog_module, "MAX_OBJECTS", 10_000)

            async def invalid(**kwargs):
                return DiskPage((DiskItem("disk:/work/x.txt", "x.txt", "file"),), 55)

            monkeypatch.setattr(scan, "list_owned_folder", invalid)
            result = await scan.resume_catalog_scan(**args, client=client)
            assert result == {"state": "error", "error_code": "invalid_provider_response", "next_offset": 0}
            assert (await manager.list_page(**args))["items"] == []


async def test_provider_offset_limit_is_not_reported_complete(data):
    def handle(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=provider_page(offset, ["x.txt"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder="/work", page_size=1)
            async with async_session_maker() as session, session.begin():
                await session.execute(
                    update(FileCatalogScan)
                    .where(FileCatalogScan.connection_id == args["connection_id"])
                    .values(next_offset=1_000_000)
                )
            assert (await scan.resume_catalog_scan(**args, client=client))["state"] == "limited"
