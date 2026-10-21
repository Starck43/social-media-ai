"""Real shell/attention acceptance; existing target and owned data, no bootstrap."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

# isort: off
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from sqlalchemy import text

# Import the shared target guard before application imports. It redirects once.
from tests.web.test_merged_home_acceptance import actor, existing_target, home

from app.core.permissions import operator_permission_scope
from app.core.config import settings
from app.core.database import async_engine
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Notification
from app.models.managers.tenant_manager import TenantUserManager
from app.services.social import connections
from app.services.notifications.attention import AttentionRepository

from tests.web.test_web_settings import _client, _csrf, _login

# isort: on

pytestmark = pytest.mark.tenancy


@pytest.fixture(scope="module", autouse=True)
async def notification_target(existing_target):
    async with async_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            present = await connection.scalar(
                text("SELECT to_regclass(:table)"),
                {"table": f"{settings.DB_SCHEMA}.{Notification.__table__.name}"},
            )
            assert present is not None, "Existing notification table is required; never bootstrap here"


@pytest.fixture
async def attention(home, existing_target):
    users, workspaces, sources = home
    rows = []

    async def event(workspace, entity, entity_id, kind="REPORT_READY"):
        with tenant_scope(bypass=True), operator_permission_scope():
            row = await Notification.objects.create(
                tenant_id=workspace,
                title="private-provider-token",
                message="raw exception <script>storedSecret()</script>",
                notification_type=kind,
                related_entity_type=entity,
                related_entity_id=entity_id,
                is_read=False,
            )
        rows.append(row)
        return row

    try:
        own = await event(workspaces[0], "source", sources[0].id)
        foreign = await event(workspaces[1], "source", sources[1].id)
        wrong_reference = await event(workspaces[0], "source", sources[1].id)
        # An owned task is deleted to make a genuine stale reference without
        # inventing an ID that could exist in the shared database.
        with tenant_scope(bypass=True), operator_permission_scope():
            task = await AgentTask.objects.filter(tenant_id=workspaces[0]).first()
            await AgentTask.objects.delete_by_id(task.id)
        deleted = await event(workspaces[0], "task", task.id)
        yield home, event, own, foreign, wrong_reference, deleted
    finally:
        with tenant_scope(bypass=True), operator_permission_scope():
            for row in rows:
                await Notification.objects.delete_by_id(row.id)


async def snapshot(client, **kwargs):
    response = await client.get("/app/notifications", **kwargs)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["complete"] is True and payload["unavailable"] == []
    assert payload["count"] == len(payload["notifications"])
    assert "private-provider-token" not in response.text and "storedSecret" not in response.text
    return payload


def event_ids(payload):
    return {item["id"] for item in payload["notifications"] if item["kind"] == "notification"}


@pytest.mark.parametrize("level", ["viewer", "manager", "owner", "operator"])
async def test_real_shell_roles_and_workspace_snapshot(attention, level):
    data, event, own, foreign, wrong, deleted = attention
    user = await actor(data, level)
    _, workspaces, _ = data
    with tenant_scope(bypass=True), operator_permission_scope():
        task = await AgentTask.objects.filter(tenant_id=workspaces[0]).first()
        await AgentTask.objects.update_by_id(task.id, last_status="failed", last_run_at=datetime.now(timezone.utc))
    task_event = await event(workspaces[0], "task", task.id)
    async with await _client() as client:
        await _login(client, user.username)
        page = await client.get("/app/")
        assert page.status_code == 200 and page.text.count("<h1") == 1
        assert "notificationsBell(" in page.text
        assert ("data-invite-open" in page.text) == (level in {"owner", "operator"})
        payload = await snapshot(client, params={"tenant_id": workspaces[1]})
        assert payload["context"] == {"workspace_id": workspaces[0], "user_id": user.id}
        assert own.id in event_ids(payload)
        assert not {foreign.id, wrong.id, deleted.id} & event_ids(payload)
        assert (task_event.id in event_ids(payload)) == (level in {"owner", "operator"})
        assert any(item["kind"] == "task" for item in payload["notifications"]) == (level in {"owner", "operator"})
        source_item = next(item for item in payload["notifications"] if item.get("id") == own.id)
        assert source_item["read_id"] == (own.id if level in {"manager", "operator"} else None)
        if level in {"viewer", "manager"}:
            response = await client.post(
                f"/app/notifications/read/{task_event.id}",
                data={"_csrf": await _csrf(client, "/app/"), "workspace_id": str(workspaces[0])},
            )
            assert response.status_code == 200 and response.json()["ok"] is False
        limited = await snapshot(client, params={"limit": 1})
        assert limited["count"] == 1 and limited["has_more"] == (payload["count"] > 1)
        response = await client.post(
            f"/app/notifications/read/{own.id}",
            data={"_csrf": await _csrf(client, "/app/"), "workspace_id": str(workspaces[0])},
        )
        assert response.status_code == 200 and response.json()["ok"] == (level in {"manager", "operator"})


async def test_read_actions_share_visibility_and_do_not_resolve_failed_task(attention):
    data, event, own, foreign, wrong, deleted = attention
    users, workspaces, _ = data
    user = await actor(data, "operator")
    with tenant_scope(bypass=True), operator_permission_scope():
        task = await AgentTask.objects.filter(tenant_id=workspaces[0]).first()
        await AgentTask.objects.update_by_id(
            task.id, last_status="failed", last_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    failure = await event(workspaces[0], "task", task.id, "API_ERROR")
    async with await _client() as client:
        await _login(client, user.username)
        token = await _csrf(client, "/app/")
        form = {"_csrf": token, "workspace_id": str(workspaces[0])}
        before = await snapshot(client)
        signals = [item for item in before["notifications"] if item["kind"] == "task"]
        assert len(signals) == 1 and signals[0]["read_id"] == failure.id
        assert failure.id not in event_ids(before)
        for row in (foreign, wrong, deleted):
            response = await client.post(f"/app/notifications/read/{row.id}", data=form)
            assert response.status_code == 200 and response.json()["ok"] is False
        for _ in range(2):
            response = await client.post(f"/app/notifications/read/{own.id}", data=form)
            assert response.status_code == 200 and response.json()["ok"] is True
        response = await client.post("/app/notifications/read-all", data=form)
        assert response.status_code == 200 and response.json()["marked"] == 1
        after = await snapshot(client)
        assert not event_ids(after)
        signals = [item for item in after["notifications"] if item["kind"] == "task"]
        assert len(signals) == 1 and signals[0]["read_id"] is None
    with tenant_scope(bypass=True), operator_permission_scope():
        for row in (own, failure):
            assert (await Notification.objects.get(id=row.id)).is_read is True
        for row in (foreign, wrong, deleted):
            assert (await Notification.objects.get(id=row.id)).is_read is False
        assert (await AgentTask.objects.get(id=task.id)).last_status == "failed"


async def test_partial_feed_reports_fresh_task_identity_and_recovery(attention, monkeypatch):
    data, create_event, *_ = attention
    user = await actor(data, "operator")
    _, workspaces, _ = data
    with tenant_scope(bypass=True), operator_permission_scope():
        task = await AgentTask.objects.filter(tenant_id=workspaces[0]).first()
        await AgentTask.objects.update_by_id(
            task.id, last_status="failed", last_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    row = await create_event(workspaces[0], "task", task.id, "API_ERROR")
    original = AttentionRepository.task_failures
    monkeypatch.setattr(AttentionRepository, "task_failures", AsyncMock(side_effect=RuntimeError("fixture outage")))
    async with await _client() as client:
        await _login(client, user.username)
        payload = (await client.get("/app/notifications")).json()
        assert payload["complete"] is False and payload["unavailable"] == ["tasks"]
        assert payload["count"] == len(payload["notifications"])
        item = next(item for item in payload["notifications"] if item.get("id") == row.id)
        assert item["task_id"] == task.id
        assert item["failure_key"] == item["current_task_key"]
        assert item["current_task_key"].startswith(f"task:{workspaces[0]}:{task.id}:failed:")
        with tenant_scope(bypass=True), operator_permission_scope():
            await AgentTask.objects.update_by_id(task.id, last_status="ok")
        payload = (await client.get("/app/notifications")).json()
        item = next(item for item in payload["notifications"] if item.get("id") == row.id)
        assert item["current_task_key"] is None and item["failure_key"] is None
        monkeypatch.setattr(AttentionRepository, "task_failures", original)
        recovered = await snapshot(client)
        assert row.id in event_ids(recovered)  # Resolution does not read the persisted event.


async def test_stale_context_and_csrf_reject_writes(attention):
    data, _, own, *_ = attention
    users, workspaces, _ = data
    async with await _client() as client:
        await _login(client, users[0].username)
        token = await _csrf(client, "/app/")
        for headers in ({"X-App-Workspace": str(workspaces[1])}, {"X-App-User": str(users[1].id)}):
            response = await client.get("/app/notifications", headers=headers)
            assert response.status_code == 409 and response.json()["reset"] is True
        for form, headers in (
            ({"workspace_id": str(workspaces[0])}, {}),
            ({"_csrf": token, "workspace_id": str(workspaces[1])}, {}),
            ({"_csrf": token, "workspace_id": str(workspaces[0])}, {"X-App-User": str(users[1].id)}),
        ):
            response = await client.post(f"/app/notifications/read/{own.id}", data=form, headers=headers)
            assert response.status_code == 403
    with tenant_scope(bypass=True), operator_permission_scope():
        assert (await Notification.objects.get(id=own.id)).is_read is False


async def test_role_and_membership_revocation_are_checked_on_each_request(attention):
    data, _, own, *_ = attention
    user = await actor(data, "viewer")
    _, workspaces, _ = data
    membership = (await TenantUserManager().web_memberships(user.id))[0]
    async with await _client() as client:
        await _login(client, user.username)
        token = await _csrf(client, "/app/")
        assert own.id in event_ids(await snapshot(client))
        await TenantUserManager().update_by_id(membership.id, role_id=None)
        assert not event_ids(await snapshot(client))
        form = {"_csrf": token, "workspace_id": str(workspaces[0])}
        response = await client.post(f"/app/notifications/read/{own.id}", data=form)
        assert response.status_code == 200 and response.json()["ok"] is False
        assert (await client.post("/app/notifications/read-all", data=form)).status_code == 403
        await TenantUserManager().update_by_id(membership.id, is_active=False)
        for method, path in (
            ("GET", "/app/notifications"),
            ("POST", f"/app/notifications/read/{own.id}"),
            ("POST", "/app/notifications/read-all"),
        ):
            response = await client.request(method, path, data=form if method == "POST" else None)
            assert response.status_code == 200 and response.url.path == "/app/invite"
            assert response.history[0].status_code == 302
            assert response.history[0].headers["location"] == "/app/invite"
            assert "notificationsBell(" not in response.text
    with tenant_scope(bypass=True), operator_permission_scope():
        assert (await Notification.objects.get(id=own.id)).is_read is False


async def test_personal_connection_query_uses_caller_without_changing_workspace_feed(attention, monkeypatch):
    data, _, own, *_ = attention
    users, workspaces, _ = data
    member = await actor(data, "viewer")
    query = AsyncMock(wraps=connections.statuses_needing_action)
    monkeypatch.setattr(connections, "statuses_needing_action", query)
    for user in (users[0], member):
        async with await _client() as client:
            await _login(client, user.username)
            query.reset_mock()
            payload = await snapshot(client)
            query.assert_awaited_once_with(user.id)
            assert own.id in event_ids(payload)
            for item in payload["notifications"]:
                if item["kind"] == "connection":
                    assert item["scope"] == "personal" and item["read_id"] is None
                    assert item["key"].startswith(f"connection:{workspaces[0]}:{user.id}:")
