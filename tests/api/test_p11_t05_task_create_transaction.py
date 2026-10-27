"""P11-T05 task creation unit-of-work persistence and rollback checks."""

from __future__ import annotations

import secrets

import pytest

from app.core.tenant_context import tenant_scope
from app.models.managers.agent_task_manager import AgentTaskManager
from tests.api.test_api_permissions import _auth_headers, _client, _drop, _platform_value, _register_owner


def _plan(*, scope: str, source_ids: list[int], trigger: dict) -> dict:
    return {
        "version": 1,
        "goal": "P11-T05 transaction boundary",
        "platforms": ["vk"],
        "source_scope": scope,
        "source_ids": source_ids,
        "collection": {},
        "window": {"type": "rolling", "days": 1},
        "trigger": trigger,
    }


async def _create_source(client, headers: dict[str, str]) -> int:
    response = await client.post(
        "/api/v1/sources",
        json={
            "name": f"p11-t05-source-{secrets.token_hex(4)}",
            "platform_id": await _platform_value(),
            "source_type": "USER",
            "external_id": secrets.token_hex(6),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.tenancy
@pytest.mark.parametrize("fail_after_write", [False, True], ids=["before-links", "after-links"])
async def test_task_and_source_links_rollback_as_one_unit(monkeypatch, fail_after_write: bool) -> None:
    """A source-link failure never leaves a task whose empty links mean all sources."""
    from app.models.managers.agent_task_manager import AgentTaskManager

    original = AgentTaskManager.set_sources

    async def fail_during_link_write(self, task_id, source_ids, session=None):
        if fail_after_write:
            await original(self, task_id, source_ids, session=session)
        raise ValueError("injected source-link failure")

    monkeypatch.setattr(AgentTaskManager, "set_sources", fail_during_link_write)

    async with await _client() as client:
        owner = None
        tenant_id = None
        source_id = None
        try:
            owner, tenant_id = await _register_owner(client, "P11T05Rollback")
            headers = await _auth_headers(client, owner.username)
            source_id = await _create_source(client, headers)
            name = f"p11-t05-rollback-{secrets.token_hex(4)}"
            response = await client.post(
                "/api/v1/tasks",
                json={
                    "name": name,
                    "job_type": "collect",
                    "task_plan": _plan(
                        scope="selected",
                        source_ids=[source_id],
                        trigger={"type": "schedule", "cron": "0 9 * * *", "timezone": "UTC"},
                    ),
                    "is_active": False,
                },
                headers=headers,
            )
            assert response.status_code == 400, response.text
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().filter(name=name).first() is None
        finally:
            if source_id is not None and owner is not None:
                headers = await _auth_headers(client, owner.username)
                await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)


@pytest.mark.tenancy
async def test_task_create_scopes_and_triggers_persist_through_uow() -> None:
    """Selected, all-source and discovery scopes preserve their link semantics."""
    async with await _client() as client:
        owner = None
        tenant_id = None
        source_id = None
        created_ids: list[int] = []
        try:
            owner, tenant_id = await _register_owner(client, "P11T05Scopes")
            headers = await _auth_headers(client, owner.username)
            source_id = await _create_source(client, headers)
            cases = [
                (
                    "selected-schedule",
                    "selected",
                    [source_id],
                    {"type": "schedule", "cron": "0 9 * * *", "timezone": "UTC"},
                    [source_id],
                ),
                ("all-once", "all", [], {"type": "once"}, []),
                (
                    "discovery-schedule",
                    "discovery",
                    [],
                    {"type": "schedule", "cron": "0 10 * * *", "timezone": "UTC"},
                    [],
                ),
            ]
            for label, scope, plan_source_ids, trigger, expected_links in cases:
                name = f"p11-t05-{label}-{secrets.token_hex(3)}"
                response = await client.post(
                    "/api/v1/tasks",
                    json={
                        "name": name,
                        "job_type": "collect",
                        "task_plan": _plan(scope=scope, source_ids=plan_source_ids, trigger=trigger),
                        "is_active": False,
                    },
                    headers=headers,
                )
                assert response.status_code == 201, response.text
                task_id = response.json()["id"]
                created_ids.append(task_id)
                assert response.json()["payload"]["task_plan"]["source_scope"] == scope
                with tenant_scope(tenant_id):
                    assert await AgentTaskManager().get_sources(task_id) == expected_links
        finally:
            if owner is not None:
                headers = await _auth_headers(client, owner.username)
                for task_id in created_ids:
                    await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
                if source_id is not None:
                    await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)


def _task_values(name: str) -> dict:
    from datetime import datetime, timedelta, timezone

    return {
        "name": name,
        "cron_expr": "@once",
        "job_type": "collect",
        "payload": {},
        "is_active": False,
        "next_run_at": datetime.now(timezone.utc) + timedelta(minutes=1),
    }


async def test_duplicate_concurrent_creates_commit_one_task() -> None:
    """Independent UoWs rely on the tenant/name unique constraint for one winner."""
    import asyncio

    from sqlalchemy.exc import IntegrityError

    from app.services.task_creation import create_task_with_sources

    async with await _client() as client:
        owner = None
        tenant_id = None
        source_id = None
        name = None
        try:
            owner, tenant_id = await _register_owner(client, "P11T05Duplicate")
            headers = await _auth_headers(client, owner.username)
            source_id = await _create_source(client, headers)
            name = f"p11-t05-concurrent-{secrets.token_hex(4)}"

            async def create_once():
                with tenant_scope(tenant_id):
                    return await create_task_with_sources(
                        task_values=_task_values(name), source_ids=[source_id], tenant_id=tenant_id
                    )

            results = await asyncio.gather(create_once(), create_once(), return_exceptions=True)
            assert sum(not isinstance(result, BaseException) for result in results) == 1
            assert sum(isinstance(result, IntegrityError) for result in results) == 1
            with tenant_scope(tenant_id):
                matches = await AgentTaskManager().filter(name=name).all()
                assert len(matches) == 1
                assert await AgentTaskManager().get_sources(matches[0].id) == [source_id]
        finally:
            if owner is not None:
                headers = await _auth_headers(client, owner.username)
                if name is not None:
                    with tenant_scope(tenant_id):
                        matches = await AgentTaskManager().filter(name=name).all()
                    for task in matches:
                        await client.delete(f"/api/v1/tasks/{task.id}", headers=headers)
                if source_id is not None:
                    await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)


async def test_foreign_workspace_source_rolls_back_task_and_same_name_is_tenant_scoped() -> None:
    """A foreign source cannot cross tenant scope; task names remain tenant local."""
    from app.services.task_creation import create_task_with_sources

    async with await _client() as client:
        owner_a = owner_b = None
        tenant_a = tenant_b = None
        source_b = None
        task_a = task_b = None
        try:
            owner_a, tenant_a = await _register_owner(client, "P11T05TenantA")
            owner_b, tenant_b = await _register_owner(client, "P11T05TenantB")
            headers_b = await _auth_headers(client, owner_b.username)
            source_b = await _create_source(client, headers_b)

            shared_name = f"p11-t05-tenant-name-{secrets.token_hex(3)}"
            with tenant_scope(tenant_a):
                with pytest.raises(ValueError, match="workspace changed"):
                    await create_task_with_sources(
                        task_values=_task_values(shared_name), source_ids=None, tenant_id=tenant_b
                    )
                assert await AgentTaskManager().filter(name=shared_name).first() is None
                with pytest.raises(ValueError, match="same workspace"):
                    await create_task_with_sources(
                        task_values=_task_values(shared_name),
                        source_ids=[source_b],
                        tenant_id=tenant_a,
                    )
                assert await AgentTaskManager().filter(name=shared_name).first() is None
                task_a = await create_task_with_sources(
                    task_values=_task_values(shared_name), source_ids=None, tenant_id=tenant_a
                )
            with tenant_scope(tenant_b):
                task_b = await create_task_with_sources(
                    task_values=_task_values(shared_name), source_ids=None, tenant_id=tenant_b
                )
                assert task_b.tenant_id == tenant_b
                assert await AgentTaskManager().filter(name=shared_name).count() == 1
            assert task_a.tenant_id == tenant_a
        finally:
            if owner_a is not None:
                headers_a = await _auth_headers(client, owner_a.username)
                if task_a is not None:
                    await client.delete(f"/api/v1/tasks/{task_a.id}", headers=headers_a)
            if owner_b is not None:
                headers_b = await _auth_headers(client, owner_b.username)
                if task_b is not None:
                    await client.delete(f"/api/v1/tasks/{task_b.id}", headers=headers_b)
                if source_b is not None:
                    await client.delete(f"/api/v1/sources/{source_b}", headers=headers_b)
            await _drop(owner_a, tenant_a)
            await _drop(owner_b, tenant_b)


async def test_legacy_oneoff_rolls_back_source_failure_and_keeps_inactive_task_on_queue_failure(
    monkeypatch,
) -> None:
    """The one-off row/links commit before enqueue, which remains recoverable on queue failure."""
    from unittest.mock import AsyncMock

    from sqlalchemy import select

    from app.api.v1.endpoints.tasks import run_task_oneoff
    from app.core.database import async_session_maker
    from app.models import AgentTask

    async with await _client() as client:
        owner = None
        tenant_id = None
        source_id = None
        persisted_ids: set[int] = set()
        baseline_ids: set[int] = set()
        try:
            owner, tenant_id = await _register_owner(client, "P11T05Oneoff")
            headers = await _auth_headers(client, owner.username)
            source_id = await _create_source(client, headers)
            original_set_sources = AgentTaskManager.set_sources
            enqueue = AsyncMock()
            dispatch = AsyncMock()
            monkeypatch.setattr("app.jobs.enqueue.enqueue_task_run", enqueue)
            monkeypatch.setattr("app.jobs.dispatcher.run_job_now", dispatch)

            async def tenant_tasks() -> dict[int, AgentTask]:
                async with async_session_maker() as session:
                    rows = await session.execute(select(AgentTask).where(AgentTask.tenant_id == tenant_id))
                    return {task.id: task for task in rows.scalars()}

            before = await tenant_tasks()
            baseline_ids = set(before)

            async def fail_after_links(self, task_id, source_ids, session=None):
                await original_set_sources(self, task_id, source_ids, session=session)
                raise ValueError("injected link failure")

            monkeypatch.setattr(AgentTaskManager, "set_sources", fail_after_links)
            with tenant_scope(tenant_id), pytest.raises(ValueError, match="injected link failure"):
                await run_task_oneoff("collect", {}, [source_id], None, _user=owner)
            after_rollback = await tenant_tasks()
            assert set(after_rollback) == set(before)
            enqueue.assert_not_awaited()
            dispatch.assert_not_awaited()

            monkeypatch.setattr(AgentTaskManager, "set_sources", original_set_sources)
            enqueue.side_effect = ValueError("queue unavailable")
            with tenant_scope(tenant_id), pytest.raises(ValueError, match="queue unavailable"):
                await run_task_oneoff("collect", {}, [source_id], None, _user=owner)

            after_queue_failure = await tenant_tasks()
            persisted_ids = set(after_queue_failure) - set(before)
            assert len(persisted_ids) == 1
            recovered = after_queue_failure[next(iter(persisted_ids))]
            assert recovered.is_active is False
            assert recovered.next_run_at is None
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(recovered.id) == [source_id]
            dispatch.assert_not_awaited()
        finally:
            if owner is not None:
                headers = await _auth_headers(client, owner.username)
                current = await tenant_tasks() if tenant_id is not None else {}
                for task_id in set(current) - baseline_ids:
                    await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
                if source_id is not None:
                    await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)


async def test_payer_consent_failure_keeps_committed_plan_inactive(monkeypatch) -> None:
    """A post-commit payer failure leaves a recoverable inactive draft."""
    from unittest.mock import AsyncMock

    from app.models import AgentTask
    from app.models.managers.personal_billing_manager import PersonalBillingUnavailable

    async with await _client() as client:
        owner = None
        tenant_id = None
        task_id = None
        try:
            owner, tenant_id = await _register_owner(client, "P11T05Payer")
            headers = await _auth_headers(client, owner.username)
            monkeypatch.setattr(
                "app.services.billing.consent_current_user_as_task_payer",
                AsyncMock(side_effect=PersonalBillingUnavailable("billing unavailable")),
            )
            name = f"p11-t05-payer-{secrets.token_hex(4)}"
            response = await client.post(
                "/api/v1/tasks",
                json={
                    "name": name,
                    "job_type": "collect",
                    "task_plan": _plan(
                        scope="all",
                        source_ids=[],
                        trigger={"type": "once"},
                    ),
                    "is_active": True,
                },
                headers=headers,
            )
            assert response.status_code == 409, response.text
            with tenant_scope(tenant_id):
                task = await AgentTask.objects.filter(name=name).first()
                assert task is not None
                task_id = task.id
                assert task.is_active is False
                assert await AgentTaskManager().get_sources(task.id) == []
        finally:
            if task_id is not None and owner is not None:
                headers = await _auth_headers(client, owner.username)
                await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
            await _drop(owner, tenant_id)


async def test_persist_task_create_requires_caller_owned_transaction() -> None:
    """The explicit-session primitive refuses work without an active UoW."""
    from types import SimpleNamespace

    from app.services.task_creation import persist_task_create

    session = SimpleNamespace(in_transaction=lambda: False)
    with pytest.raises(RuntimeError, match="active transaction"):
        await persist_task_create(session, task_values=_task_values("must-not-write"), source_ids=None)


async def test_legacy_cli_create_rolls_back_task_when_source_link_write_fails(monkeypatch) -> None:
    """The CLI legacy create path keeps task and source association in one transaction."""
    from app.models import AgentTask
    from cli.main import _add_task

    async with await _client() as client:
        owner = None
        tenant_id = None
        source_id = None
        name = f"p11-t05-cli-rollback-{secrets.token_hex(4)}"
        try:
            owner, tenant_id = await _register_owner(client, "P11T05CLI")
            headers = await _auth_headers(client, owner.username)
            source_id = await _create_source(client, headers)
            original_set_sources = AgentTaskManager.set_sources

            async def fail_after_links(self, task_id, source_ids, session=None):
                await original_set_sources(self, task_id, source_ids, session=session)
                raise ValueError("injected CLI link failure")

            monkeypatch.setattr(AgentTaskManager, "set_sources", fail_after_links)
            with tenant_scope(tenant_id), pytest.raises(ValueError, match="injected CLI link failure"):
                await _add_task(name, "0 9 * * *", "collect", {}, [source_id], None, str(tenant_id))
            with tenant_scope(tenant_id):
                assert await AgentTask.objects.filter(name=name).first() is None
        finally:
            if source_id is not None and owner is not None:
                headers = await _auth_headers(client, owner.username)
                await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)
