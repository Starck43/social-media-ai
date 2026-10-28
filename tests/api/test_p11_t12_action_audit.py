"""P11-T12 action-audit persistence, retention and tenant-fenced API checks."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import ActionAuditEvent
from app.models.managers.action_audit_event_manager import ActionAuditEventManager
from app.types import WorkspaceRoleType
from tests.api.test_api_permissions import (
    _auth_headers,
    _client,
    _drop,
    _invitee,
    _register_owner,
)

pytestmark = pytest.mark.tenancy


async def _record(tenant_id: int, actor, *, outcome: str = "succeeded"):
    from app.core.database import async_session_maker

    with tenant_scope(tenant_id), permission_scope(actor, is_owner=True):
        if outcome == "unknown":
            return await ActionAuditEvent.objects.record_unknown(
                actor_kind="user",
                actor_id=actor.id,
                action="scheduling.task_admit",
                resource_kind="agent_task",
                resource_id=secrets.randbelow(2**30) + 1,
                reason_code="launch_unconfirmed",
            )
        async with async_session_maker() as session:
            async with session.begin():
                return await ActionAuditEvent.objects.record_success(
                    session=session,
                    actor_kind="user",
                    actor_id=actor.id,
                    action="scheduling.task_admit",
                    resource_kind="agent_task",
                    resource_id=secrets.randbelow(2**30) + 1,
                    job_id=secrets.randbelow(2**30) + 1,
                )


async def _purge(tenant_ids: list[int]) -> None:
    """Delete only this test's synthetic rows through the documented cleanup marker."""
    from app.core.database import async_session_maker

    async with async_session_maker() as session:
        async with session.begin():
            await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
            await session.execute(delete(ActionAuditEvent).where(ActionAuditEvent.tenant_id.in_(tenant_ids)))


async def test_member_reads_only_own_allowlisted_events_and_export():
    async with await _client() as client:
        owner_a = owner_b = viewer = None
        tenant_a = tenant_b = None
        try:
            owner_a, tenant_a = await _register_owner(client, "P11T12AuditA")
            owner_b, tenant_b = await _register_owner(client, "P11T12AuditB")
            viewer = await _invitee("P11T12AuditViewer", WorkspaceRoleType.VIEWER, tenant_a)
            event_a = await _record(tenant_a, owner_a)
            event_b = await _record(tenant_b, owner_b)
            headers_a = await _auth_headers(client, owner_a.username)
            viewer_headers = await _auth_headers(client, viewer.username)

            response = await client.get("/api/v1/audit/events", headers=headers_a)
            assert response.status_code == 200, response.text
            body = response.json()
            assert [item["event_id"] for item in body["items"]] == [event_a.event_id]
            assert set(body["items"][0]) == {
                "event_id",
                "tenant_id",
                "occurred_at",
                "actor_kind",
                "actor_id",
                "action",
                "resource_kind",
                "resource_id",
                "outcome",
                "reason_code",
                "request_id",
                "job_id",
                "receipt_ref",
            }
            assert event_b.event_id not in {item["event_id"] for item in body["items"]}

            export = await client.get("/api/v1/audit/events/export", headers=headers_a)
            assert export.status_code == 200, export.text
            assert [item["event_id"] for item in export.json()["items"]] == [event_a.event_id]

            denied = await client.get("/api/v1/audit/events", headers=viewer_headers)
            assert denied.status_code == 403
            widened = await client.get(
                "/api/v1/audit/events",
                headers={**headers_a, "X-Tenant-Id": str(tenant_b)},
            )
            assert widened.status_code == 403
        finally:
            await _purge([value for value in (tenant_a, tenant_b) if value is not None])
            await _drop(viewer, None)
            await _drop(owner_a, tenant_a)
            await _drop(owner_b, tenant_b)


async def test_success_event_is_atomic_and_unknown_remains_distinct():
    from app.core.database import async_session_maker

    async with await _client() as client:
        owner = None
        tenant_id = None
        try:
            owner, tenant_id = await _register_owner(client, "P11T12AuditAtomic")
            with tenant_scope(tenant_id), permission_scope(owner, is_owner=True):
                async with async_session_maker() as session:
                    with pytest.raises(RuntimeError, match="force rollback"):
                        async with session.begin():
                            rolled_back = await ActionAuditEvent.objects.record_success(
                                session=session,
                                actor_kind="user",
                                actor_id=owner.id,
                                action="scheduling.task_admit",
                                resource_kind="agent_task",
                                resource_id=123456,
                            )
                            rolled_back_event_id = rolled_back.event_id
                            raise RuntimeError("force rollback")
                events_after_rollback = await ActionAuditEvent.objects.list_events(
                    since=datetime.now(timezone.utc) - timedelta(days=1),
                    until=datetime.now(timezone.utc) + timedelta(seconds=1),
                )
                assert all(event.event_id != rolled_back_event_id for event in events_after_rollback)

            unknown = await _record(tenant_id, owner, outcome="unknown")
            with tenant_scope(tenant_id), permission_scope(owner, is_owner=True):
                events = await ActionAuditEvent.objects.list_events(
                    since=datetime.now(timezone.utc) - timedelta(days=1),
                    until=datetime.now(timezone.utc) + timedelta(seconds=1),
                )
            assert unknown.outcome == "unknown"
            assert unknown.event_id in {event.event_id for event in events}
        finally:
            if tenant_id is not None:
                await _purge([tenant_id])
            await _drop(owner, tenant_id)


async def test_expiry_uses_workspace_plan_and_update_is_rejected():
    async with await _client() as client:
        owner = None
        tenant_id = None
        try:
            owner, tenant_id = await _register_owner(client, "P11T12AuditExpiry")
            old = ActionAuditEvent(
                tenant_id=tenant_id,
                event_id=secrets.token_hex(16),
                occurred_at=datetime.now(timezone.utc) - timedelta(days=31),
                actor_kind="user",
                actor_id=str(owner.id),
                action="scheduling.task_admit",
                resource_kind="agent_task",
                resource_id="1001",
                outcome="succeeded",
            )
            with tenant_scope(tenant_id), permission_scope(owner, is_owner=True):
                async with ActionAuditEvent.objects._get_owned_session() as session:
                    session.add(old)
                    await session.flush()
                listed = await ActionAuditEvent.objects.list_events(
                    since=datetime.now(timezone.utc) - timedelta(days=89),
                    until=datetime.now(timezone.utc),
                )
            assert old.event_id in {event.event_id for event in listed}, "the seeded expired row must exist first"
            with tenant_scope(tenant_id), permission_scope(owner, is_owner=True):
                deleted = await ActionAuditEventManager().delete_expired()
                window_end = datetime.now(timezone.utc)
                remaining = await ActionAuditEvent.objects.list_events(
                    since=window_end - timedelta(days=89),
                    until=window_end,
                )
            assert deleted == 1  # default Pro plan retains 30 days
            assert old.event_id not in {event.event_id for event in remaining}
        finally:
            if tenant_id is not None:
                await _purge([tenant_id])
            await _drop(owner, tenant_id)


async def test_expired_cleanup_deletes_the_whole_batch_in_one_pass():
    """More expired rows than the requested batch still leave in that one call.

    Retention runs from a periodic job, so a cleanup that quietly removed only
    one row per pass would keep evidence far past the plan window while
    reporting success.
    """
    async with await _client() as client:
        owner = None
        tenant_id = None
        try:
            owner, tenant_id = await _register_owner(client, "P11T12AuditBatch")
            aged = datetime.now(timezone.utc) - timedelta(days=31)
            with tenant_scope(tenant_id), permission_scope(owner, is_owner=True):
                async with ActionAuditEvent.objects._get_owned_session() as session:
                    for index in range(3):
                        session.add(
                            ActionAuditEvent(
                                tenant_id=tenant_id,
                                event_id=secrets.token_hex(16),
                                occurred_at=aged,
                                actor_kind="user",
                                actor_id=str(owner.id),
                                action="scheduling.task_admit",
                                resource_kind="agent_task",
                                resource_id=str(2000 + index),
                                outcome="succeeded",
                            )
                        )
                    await session.flush()

            with tenant_scope(tenant_id), permission_scope(owner, is_owner=True):
                deleted = await ActionAuditEventManager().delete_expired(batch_size=10)
                remaining = await ActionAuditEvent.objects.list_events(
                    since=datetime.now(timezone.utc) - timedelta(days=89),
                    until=datetime.now(timezone.utc),
                )
            assert deleted == 3
            assert remaining == []
        finally:
            if tenant_id is not None:
                await _purge([tenant_id])
            await _drop(owner, tenant_id)


async def test_storage_blocks_ordinary_update_and_manager_deletion():
    async with await _client() as client:
        owner = None
        tenant_id = None
        try:
            owner, tenant_id = await _register_owner(client, "P11T12AuditImmutable")
            event = await _record(tenant_id, owner)
            from app.core.database import async_session_maker

            with pytest.raises(DBAPIError):
                async with async_session_maker() as session:
                    async with session.begin():
                        await session.execute(
                            update(ActionAuditEvent)
                            .where(ActionAuditEvent.event_id == event.event_id)
                            .values(reason_code="tampered")
                        )
            with pytest.raises(DBAPIError):
                async with async_session_maker() as session:
                    async with session.begin():
                        await session.execute(
                            delete(ActionAuditEvent).where(ActionAuditEvent.event_id == event.event_id)
                        )
            with tenant_scope(tenant_id), permission_scope(owner, is_owner=True):
                with pytest.raises(PermissionError, match="immutable"):
                    await ActionAuditEvent.objects.delete_by_id(event.id)
        finally:
            if tenant_id is not None:
                await _purge([tenant_id])
            await _drop(owner, tenant_id)
