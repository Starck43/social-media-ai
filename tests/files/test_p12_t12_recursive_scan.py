"""Synthetic recursive persistence and authority seams with MockTransport only."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, update

from app.core.database import async_session_maker
from app.integrations.file_workspace import catalog_traversal as traversal
from app.models import FileCatalogScan, WorkspaceConnection
from app.models.managers.file_catalog_manager import FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace import catalog_scan as scan
from tests.shared.file_catalog_fixtures import actor, configure, data  # noqa: F401

pytestmark = pytest.mark.tenancy
manager = FileCatalogManager()
ROOT = "disk:/work"


def response(folder, offset, entries):
    return httpx.Response(
        200,
        json={
            "path": folder,
            "type": "dir",
            "_embedded": {
                "offset": offset,
                "items": [{"path": folder + "/" + name, "name": name, "type": kind} for name, kind in entries],
            },
        },
    )


async def checkpoint(args):
    async with async_session_maker() as session:
        row = await session.scalar(
            select(FileCatalogScan).where(FileCatalogScan.connection_id == args["connection_id"])
        )
        return row.traversal, row.next_offset


async def expire(args):
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(FileCatalogScan)
            .where(FileCatalogScan.connection_id == args["connection_id"])
            .values(lease_until=datetime.now(timezone.utc) - timedelta(seconds=1))
        )


async def test_recursive_queue_survives_steps_deduplicates_and_never_lists_denied_children(data):
    calls = []
    pages = {
        (ROOT, 0): [("a", "dir"), ("a.txt", "file")],
        (ROOT, 2): [("a", "dir"), ("private", "dir")],
        (ROOT, 4): [("b", "dir")],
        (ROOT + "/a", 0): [("inside.txt", "file")],
        (ROOT + "/b", 0): [("c", "dir")],
        (ROOT + "/b/c", 0): [("deep.txt", "file")],
    }

    def handle(request):
        key = (request.url.params["path"], int(request.url.params["offset"]))
        calls.append(key)
        assert key in pages
        return response(*key, pages[key])

    await configure(data, restrictions=[{"path": ROOT + "/private", "deny": ["list"]}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder=ROOT, page_size=2, recursive=True)
            for _ in range(6):
                state = await scan.resume_catalog_scan(**args, client=client)
                assert await scan.catalog_scan_status(**args) == state
            assert state == {"state": "exhausted", "error_code": None, "next_offset": None}
            with pytest.raises(scan.ScanNotResumable):
                await scan.resume_catalog_scan(**args, client=client)
            saved = (await manager.list_page(**args))["items"]
            assert {item["path"] for item in saved} == {
                ROOT + suffix for suffix in ["/a", "/a.txt", "/b", "/a/inside.txt", "/b/c", "/b/c/deep.txt"]
            }
            assert all(item["observation_number"] == 1 for item in saved)
            value, _ = await checkpoint(args)
            assert value == {"pending": [], "seen": [ROOT, ROOT + "/a", ROOT + "/b", ROOT + "/b/c"], "pages": 6}
    assert calls == list(pages)


async def test_child_error_and_worker_interruption_preserve_folder_queue_and_offset(data, monkeypatch):
    calls, fail = [], [True]

    def handle(request):
        folder, offset = request.url.params["path"], int(request.url.params["offset"])
        calls.append((folder, offset))
        if folder == ROOT:
            return response(folder, offset, [("a", "dir")] if offset == 0 else [])
        if fail[0]:
            fail[0] = False
            return httpx.Response(429, text="synthetic credential must not surface")
        return response(folder, offset, [("x.txt", "file")] if offset == 0 else [])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder=ROOT, page_size=1, recursive=True)
            await scan.resume_catalog_scan(**args, client=client)
            await scan.resume_catalog_scan(**args, client=client)
            before = await checkpoint(args)
            failed = await scan.resume_catalog_scan(**args, client=client)
            assert failed == {"state": "error", "error_code": "rate_limited", "next_offset": 0}
            assert await checkpoint(args) == before
            original = scan.list_owned_folder

            async def interrupted(**kwargs):
                raise RuntimeError("synthetic interruption")

            monkeypatch.setattr(scan, "list_owned_folder", interrupted)
            with pytest.raises(RuntimeError):
                await scan.resume_catalog_scan(**args, client=client)
            assert (await scan.catalog_scan_status(**args))["state"] == "running"
            assert await checkpoint(args) == before
            await expire(args)
            monkeypatch.setattr(scan, "list_owned_folder", original)
            assert (await scan.resume_catalog_scan(**args, client=client))["next_offset"] == 1
            assert (await scan.resume_catalog_scan(**args, client=client))["state"] == "exhausted"
    assert calls == [(ROOT, 0), (ROOT, 1), (ROOT + "/a", 0), (ROOT + "/a", 0), (ROOT + "/a", 1)]


async def test_real_competing_mock_workers_have_one_claim_and_one_provider_read(data):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(request):
        calls.append(request.url.params["path"])
        entered.set()
        await release.wait()
        return response(ROOT, 0, [])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder=ROOT, recursive=True)
            task = asyncio.create_task(scan.resume_catalog_scan(**args, client=client))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                with pytest.raises(scan.ScanBusy):
                    await scan.resume_catalog_scan(**args, client=client)
                with pytest.raises(scan.ScanBusy):
                    await scan.start_catalog_scan(**args, folder=ROOT, recursive=True)
            finally:
                release.set()
                await asyncio.wait_for(task, timeout=5)
            assert (await scan.catalog_scan_status(**args))["state"] == "exhausted"
    assert calls == [ROOT]


@pytest.mark.parametrize("bound,value", [("MAX_SCAN_FOLDERS", 1), ("MAX_SCAN_DEPTH", 0), ("MAX_SCAN_PAGES", 1)])
async def test_recursive_bounds_are_terminal_and_hide_continuation(data, monkeypatch, bound, value):
    monkeypatch.setattr(traversal, bound, value)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response(ROOT, 0, [("a", "dir")]))
    ) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder=ROOT, recursive=True)
            assert await scan.resume_catalog_scan(**args, client=client) == {
                "state": "limited",
                "error_code": None,
                "next_offset": None,
            }
            with pytest.raises(scan.ScanNotResumable):
                await scan.resume_catalog_scan(**args, client=client)
            assert [row["name"] for row in (await manager.list_page(**args))["items"]] == ["a"]


async def test_policy_change_between_parent_and_child_requires_restart_without_network(data):
    calls = []

    def handle(request):
        calls.append(request.url.params["path"])
        return response(ROOT, 0, [("a", "dir")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder=ROOT, recursive=True)
            await scan.resume_catalog_scan(**args, client=client)
            await configure(data, restrictions=[{"path": ROOT + "/a", "deny": ["list"]}])
            assert (await scan.catalog_scan_status(**args))["state"] == "policy_changed"
            with pytest.raises(scan.ScanChanged):
                await scan.resume_catalog_scan(**args, client=client)
            assert (await manager.list_page(**args))["items"] == []
    assert calls == [ROOT]


@pytest.mark.parametrize("change", ["revoke", "policy"])
async def test_inflight_child_authority_change_cannot_admit_or_advance(data, monkeypatch, change):
    original = scan.list_owned_folder

    async def change_during_read(**kwargs):
        page = await original(**kwargs)
        if kwargs["folder"] != ROOT:
            if change == "policy":
                await configure(data, restrictions=[{"path": ROOT + "/a", "deny": ["list"]}])
            else:
                async with async_session_maker() as session, session.begin():
                    await session.execute(
                        update(WorkspaceConnection)
                        .where(WorkspaceConnection.id == kwargs["connection_id"])
                        .values(is_active=False)
                    )
        return page

    monkeypatch.setattr(scan, "list_owned_folder", change_during_read)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: response(
                req.url.params["path"],
                0,
                [("a", "dir")] if req.url.params["path"] == ROOT else [("secret.txt", "file")],
            )
        )
    ) as client:
        with actor(data) as args:
            await scan.start_catalog_scan(**args, folder=ROOT, recursive=True)
            await scan.resume_catalog_scan(**args, client=client)
            before = await checkpoint(args)
            with pytest.raises(ConnectionDenied):
                await scan.resume_catalog_scan(**args, client=client)
            assert await checkpoint(args) == before
            async with async_session_maker() as session:
                from app.models import FileCatalogObject

                assert (
                    await session.scalars(
                        select(FileCatalogObject.name).where(FileCatalogObject.connection_id == args["connection_id"])
                    )
                ).all() == ["a"]


async def test_recursive_owner_and_tenant_isolation_and_restart(data):
    with actor(data) as args:
        await scan.start_catalog_scan(**args, folder=ROOT, recursive=True)
        original = await checkpoint(args)
    for scope in ({"user": 1}, {"tenant": 1}):
        with actor(data, **scope) as foreign:
            with pytest.raises(ConnectionDenied):
                await scan._claim(**foreign)
    with actor(data, user=1, connection=1) as other:
        assert await scan.catalog_scan_status(**other) is None
        await scan.start_catalog_scan(**other, folder=ROOT, recursive=True)
    with actor(data) as args:
        assert await checkpoint(args) == original
        await scan.start_catalog_scan(**args, folder=ROOT)
        assert await checkpoint(args) == (None, 0)


async def test_corrupt_pending_queue_fails_closed_before_read(data):
    with actor(data) as args:
        await scan.start_catalog_scan(**args, folder=ROOT, recursive=True)
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(FileCatalogScan)
                .where(FileCatalogScan.connection_id == args["connection_id"])
                .values(traversal={"pending": ["disk:/outside"], "seen": [ROOT], "pages": 0})
            )
        assert (await scan.catalog_scan_status(**args))["state"] == "policy_changed"
        with pytest.raises(scan.ScanChanged):
            await scan._claim(**args)
