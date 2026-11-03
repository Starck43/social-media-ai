"""Synthetic test-schema catalog; no provider/account/network/model calls."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_maker
from app.integrations.file_workspace.yandex_disk import DiskItem, DiskPage
from app.models import FileCatalogObservation, TenantUser, User, WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers import file_catalog_manager as module
from app.models.managers.file_catalog_manager import CatalogCapacityError, CatalogInputError, FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from tests.shared.file_catalog_fixtures import actor, admit, configure, data, page  # noqa: F401

pytestmark = pytest.mark.tenancy
manager = FileCatalogManager()


async def test_replay_and_twenty_reads_keep_one_observation(data):
    with actor(data) as args:
        ticket = await manager.begin_page(**args, folder="/work")
        for _ in range(20):
            result = await manager.admit_page(**args, ticket=ticket, page=page("report.txt"))
            rows = await manager.list_page(**args)
            assert len(rows["items"]) == 1
        assert result == {"created": 0, "metadata_changed": 0}
        detail = await manager.detail(**args, object_id=rows["items"][0]["id"])
        assert detail["observation_number"] == 1
        assert len(detail["observations"]) == 1
        assert detail["identity_kind"] == "path"
        assert not detail["content_available"] and not detail["original_open_available"]
        assert not detail["content_version_known"]


async def test_repeat_new_ticket_updates_last_observed_not_history(data):
    with actor(data) as args:
        await admit(args, "report.txt")
        before = (await manager.list_page(**args))["items"][0]
        await admit(args, "report.txt")
        after = (await manager.list_page(**args))["items"][0]
        assert after["observed_at"] > before["observed_at"]
        assert after["observation_number"] == 1


async def test_changed_metadata_append_and_older_page_does_not_revert(data):
    with actor(data) as args:
        older = await manager.begin_page(**args, folder="/work")
        await admit(args, "item", kind="file")
        await admit(args, "item", kind="dir")
        await manager.admit_page(**args, ticket=older, page=page("item", kind="file"))
        row = (await manager.list_page(**args))["items"][0]
        detail = await manager.detail(**args, object_id=row["id"])
        assert row["kind"] == "dir" and row["observation_number"] == 2
        assert [r["kind"] for r in detail["observations"]] == ["dir", "file"]


async def test_partial_and_empty_pages_never_delete(data):
    with actor(data) as args:
        await admit(args, "a.txt", "b.txt")
        ticket = await manager.begin_page(**args, folder="/work")
        await manager.admit_page(**args, ticket=ticket, page=page("a.txt", next_offset=100))
        await manager.admit_page(**args, ticket=ticket, page=page())
        assert len((await manager.list_page(**args))["items"]) == 2


async def test_sql_keyset_paging_and_literal_search(data):
    with actor(data) as args:
        await admit(args, "a_under_score.txt", "b_underXscore.txt", "c.txt", "d.txt", "e.txt")
        rows, after = [], 0
        while True:
            result = await manager.list_page(**args, limit=2, after=after)
            rows.extend(result["items"])
            after = result["next_after"]
            if after is None:
                break
        assert len(rows) == len({r["id"] for r in rows}) == 5
        found = await manager.list_page(**args, query="_score")
        assert [r["name"] for r in found["items"]] == ["a_under_score.txt"]
        assert (await manager.list_page(**args, query="no result"))["items"] == []


@pytest.mark.parametrize("user,tenant", [(1, 0), (0, 1), (1, 1)])
async def test_owner_and_workspace_isolation(data, user, tenant):
    with actor(data) as args:
        await admit(args, "secret.txt")
        object_id = (await manager.list_page(**args))["items"][0]["id"]
    with actor(data, user=user, tenant=tenant) as args:
        for call in (
            manager.begin_page(**args, folder="/work"),
            manager.list_page(**args),
            manager.detail(**args, object_id=object_id),
        ):
            with pytest.raises(ConnectionDenied):
                await call


async def test_same_workspace_personal_connections_not_shared(data):
    with actor(data) as args:
        await admit(args, "owner0.txt")
        object_id = (await manager.list_page(**args))["items"][0]["id"]
    with actor(data, user=1, connection=1) as args:
        assert (await manager.list_page(**args))["items"] == []
        with pytest.raises(ConnectionDenied):
            await manager.detail(**args, object_id=object_id)
        await admit(args, "owner1.txt")
        assert [r["name"] for r in (await manager.list_page(**args))["items"]] == ["owner1.txt"]


async def test_policy_filters_cached_metadata_before_paging_and_search(data):
    with actor(data) as args:
        await admit(args, "hidden", kind="dir")
        await admit(args, "secret.txt", folder="disk:/work/hidden")
        await admit(args, "visible.txt")
        hidden_id = next(r["id"] for r in (await manager.list_page(**args))["items"] if r["name"] == "secret.txt")
        await configure(data, restrictions=[{"path": "disk:/work/hidden", "deny": ["list"]}])
        assert [r["name"] for r in (await manager.list_page(**args, limit=1))["items"]] == ["visible.txt"]
        assert (await manager.list_page(**args, query="secret"))["items"] == []
        with pytest.raises(ConnectionDenied):
            await manager.detail(**args, object_id=hidden_id)


async def test_root_change_hides_old_root_and_invalidates_ticket(data):
    with actor(data) as args:
        ticket = await manager.begin_page(**args, folder="/work")
        await admit(args, "report.txt")
        object_id = (await manager.list_page(**args))["items"][0]["id"]
        await configure(data, root="disk:/other")
        assert (await manager.list_page(**args))["items"] == []
        with pytest.raises(ConnectionDenied):
            await manager.detail(**args, object_id=object_id)
        with pytest.raises(ConnectionDenied):
            await manager.admit_page(**args, ticket=ticket, page=page("late.txt"))


@pytest.mark.parametrize(
    "change", ["disabled", "reauth", "secret", "membership", "user", "role_missing", "boundary", "policy"]
)
async def test_revocation_and_unknown_boundary_fail_closed(data, change):
    with actor(data) as args:
        await admit(args, "report.txt")
        row = (await manager.list_page(**args))["items"][0]
        ticket = await manager.begin_page(**args, folder="/work")
        async with async_session_maker() as session, session.begin():
            if change in {"disabled", "reauth"}:
                await session.execute(
                    update(WorkspaceConnection)
                    .where(WorkspaceConnection.id == args["connection_id"])
                    .values(state=change)
                )
            elif change == "secret":
                await session.execute(
                    delete(WorkspaceConnectionSecret).where(
                        WorkspaceConnectionSecret.connection_id == args["connection_id"]
                    )
                )
            elif change == "membership":
                await session.execute(
                    update(TenantUser)
                    .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                    .values(is_active=False)
                )
            elif change == "user":
                await session.execute(update(User).where(User.id == args["actor_id"]).values(is_active=False))
            elif change == "role_missing":
                await session.execute(
                    update(TenantUser)
                    .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                    .values(role_id=None)
                )
            elif change == "boundary":
                await session.execute(
                    update(WorkspaceConnection)
                    .where(WorkspaceConnection.id == args["connection_id"])
                    .values(
                        config={
                            "boundary": "local_only",
                            "file_policy": {"version": 1, "root": "/work", "restrictions": []},
                        }
                    )
                )
            else:
                await session.execute(
                    update(WorkspaceConnection)
                    .where(WorkspaceConnection.id == args["connection_id"])
                    .values(config={"file_policy": {"version": 99, "root": "/work", "restrictions": []}})
                )
        with pytest.raises(ConnectionDenied):
            await manager.list_page(**args)
        with pytest.raises(ConnectionDenied):
            await manager.detail(**args, object_id=row["id"])
        with pytest.raises(ConnectionDenied):
            await manager.admit_page(**args, ticket=ticket, page=page("late.txt"))


@pytest.mark.parametrize(
    "bad_page",
    [
        DiskPage((DiskItem("disk:/elsewhere/x", "x", "file"),), None),
        DiskPage((DiskItem("disk:/work/../x", "x", "file"),), None),
        DiskPage((DiskItem("disk:/work/x", "wrong-name", "file"),), None),
        DiskPage((DiskItem("disk:/work/x", "x", "symlink"),), None),
        page("x", "x"),
        page(*[str(i) for i in range(101)]),
        DiskPage((), True),
        DiskPage((), None, "not-a-bool"),
    ],
)
async def test_malformed_page_atomic_rejection(data, bad_page):
    with actor(data) as args:
        ticket = await manager.begin_page(**args, folder="/work")
        with pytest.raises(CatalogInputError):
            await manager.admit_page(**args, ticket=ticket, page=bad_page)
        assert (await manager.list_page(**args))["items"] == []


@pytest.mark.parametrize("delta", [timedelta(minutes=-2), timedelta(minutes=2)])
async def test_expired_or_future_ticket(data, delta):
    with actor(data) as args:
        ticket = await manager.begin_page(**args, folder="/work")
        ticket = replace(ticket, issued_at=datetime.now(timezone.utc) + delta)
        with pytest.raises(ConnectionDenied):
            await manager.admit_page(**args, ticket=ticket, page=page("x"))


async def test_policy_change_and_cross_owner_ticket(data):
    with actor(data) as args:
        ticket = await manager.begin_page(**args, folder="/work")
        await configure(data, restrictions=[{"path": "disk:/work/x", "deny": ["list"]}])
        with pytest.raises(ConnectionDenied):
            await manager.admit_page(**args, ticket=ticket, page=page("x"))
    with actor(data, user=1, connection=1) as args:
        with pytest.raises(ConnectionDenied):
            await manager.admit_page(**args, ticket=ticket, page=page("x"))


async def test_hidden_page_members_not_admitted(data):
    await configure(data, restrictions=[{"path": "disk:/work/hidden", "deny": ["list"]}])
    with actor(data) as args:
        assert await admit(args, "hidden", "ok.txt") == {"created": 1, "metadata_changed": 0}
        assert [r["name"] for r in (await manager.list_page(**args))["items"]] == ["ok.txt"]


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"after": -1}, {"query": "x" * 201}, {"query": "\n"}]
)
async def test_query_bounds(data, kwargs):
    with actor(data) as args:
        with pytest.raises(CatalogInputError):
            await manager.list_page(**args, **kwargs)


async def test_capacity_rolls_back_whole_page(data, monkeypatch):
    monkeypatch.setattr(module, "MAX_OBJECTS", 2)
    with actor(data) as args:
        await admit(args, "existing")
        with pytest.raises(CatalogCapacityError):
            await admit(args, "new1", "new2")
        assert [r["name"] for r in (await manager.list_page(**args))["items"]] == ["existing"]
        monkeypatch.setattr(module, "MAX_OBSERVATIONS", 1)
        with pytest.raises(CatalogCapacityError):
            await admit(args, "new", "existing", kind="dir")
        assert [r["name"] for r in (await manager.list_page(**args))["items"]] == ["existing"]


async def test_concurrent_admissions_are_idempotent(data):
    with actor(data) as args:
        ticket = await manager.begin_page(**args, folder="/work")
        await asyncio.gather(*[manager.admit_page(**args, ticket=ticket, page=page("item")) for _ in range(2)])
        rows = (await manager.list_page(**args))["items"]
        assert len(rows) == 1
        detail = await manager.detail(**args, object_id=rows[0]["id"])
        assert len(detail["observations"]) == 1


async def test_database_observation_immutable_and_tenant_fk(data):
    with actor(data) as args:
        await admit(args, "report.txt")
        row = (await manager.list_page(**args))["items"][0]
    with pytest.raises(IntegrityError):
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(FileCatalogObservation)
                .where(FileCatalogObservation.object_id == row["id"])
                .values(name="changed")
            )
    with pytest.raises(IntegrityError):
        async with async_session_maker() as session, session.begin():
            session.add(
                FileCatalogObservation(
                    tenant_id=data.tenants[1].id,
                    object_id=row["id"],
                    number=2,
                    name="forged",
                    kind="file",
                    observed_at=datetime.now(timezone.utc),
                )
            )
            await session.flush()


@pytest.mark.parametrize(
    "name,query,expected",
    [("Договор.pdf", "Договор", True), ("REPORT.txt", "report", True), ("100file.txt", "%", False)],
)
async def test_literal_names_and_ascii_case_search(data, name, query, expected):
    with actor(data) as args:
        await admit(args, name, "unrelated.txt")
        found = await manager.list_page(**args, query=query)
        assert [item["name"] for item in found["items"]] == ([name] if expected else [])
