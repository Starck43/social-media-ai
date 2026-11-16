"""Real shell/attention acceptance; existing target and owned data, no bootstrap."""

from __future__ import annotations

import sys
from dataclasses import replace
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
from app.models import AgentTask, Notification, NotificationReceipt
from app.models.managers.tenant_manager import TenantUserManager
from app.services.social import connections
from app.services.notifications.attention import AttentionRepository
from app.types import WorkspaceRoleType

from tests.web.test_web_settings import _client, _csrf, _invitee, _login

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
    """Workspace-origin events plus their personal receipts, as production fans out.

    The inbox is receipt-owned (`NotificationReceipt.objects.list_for_user`), so an
    archive-only `Notification` is invisible to `/app/notifications`; the trusted
    producer `publish()` creates both rows. The caller names its acting member with
    `address(user)` *before* the fixture's events are published, because a published
    event keeps the recipients it had at publish time (no back-fill later).
    """
    users, workspaces, sources = home
    addresses = []

    def address(user):
        if user.id not in addresses:
            addresses.append(user.id)

    rows = []

    async def event(workspace, entity, entity_id, kind="REPORT_READY", recipients=None):
        # `publish()` takes its origin workspace from the ambient scope and refuses a
        # bypass with no tenant, so name the event's workspace explicitly.
        with tenant_scope(workspace), operator_permission_scope():
            row = await NotificationReceipt.objects.publish(
                recipient_user_ids=list(addresses if recipients is None else recipients),
                title="private-provider-token",
                message="raw exception <script>storedSecret()</script>",
                notification_type=kind,
                related_entity_type=entity,
                related_entity_id=entity_id,
            )
        rows.append(row)
        return row

    # Events are published by `_publish()` after the caller names its member, so
    # each test's receipt set is the one the inbox is supposed to show.
    try:
        yield home, event, address, rows
    finally:
        with tenant_scope(bypass=True), operator_permission_scope():
            for row in rows:
                await Notification.objects.delete_by_id(row.id)


async def _publish(attention):
    """Publish the standard event set once the test has addressed its member.

    Returns `(home, event, own, foreign, wrong_reference, deleted)`. `own` is the
    deliverable source event; `foreign` belongs to another workspace; `wrong` is a
    source id from that other workspace; `deleted` points at a task this helper
    removes, a genuine stale reference that never invents an ID.
    """
    home, event, _, rows = attention
    users, workspaces, sources = home
    own = await event(workspaces[0], "source", sources[0].id)
    # Cross-workspace rows are addressed to their own workspace's owner: `publish`
    # refuses a recipient who is not an active member of the event's workspace.
    foreign = await event(workspaces[1], "source", sources[1].id, recipients=[users[1].id])
    wrong_reference = await event(workspaces[0], "source", sources[1].id)
    with tenant_scope(workspaces[0]), operator_permission_scope():
        task = await AgentTask.objects.filter(tenant_id=workspaces[0]).first()
        await AgentTask.objects.delete_by_id(task.id)
    deleted = await event(workspaces[0], "task", task.id)
    assert len(rows) == 4
    return home, event, own, foreign, wrong_reference, deleted


async def snapshot(client, **kwargs):
    response = await client.get("/app/notifications", **kwargs)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["complete"] is True and payload["unavailable"] == []
    assert payload["count"] == len(payload["notifications"])
    # Stored text is returned verbatim, so the protection is the media type: a
    # browser never renders this payload as HTML regardless of the stored markup.
    assert response.headers["content-type"].startswith("application/json")
    return payload


def event_ids(payload):
    return {item["id"] for item in payload["notifications"] if item["kind"] == "notification"}


async def _receipt_state(user, event_ids):
    """Read state of `user`'s receipts for the given events, per event id."""
    with tenant_scope(bypass=True), operator_permission_scope():
        rows = await NotificationReceipt.objects.filter(user_id=user.id)
    return {row.notification_id: row.read_at is not None for row in rows if row.notification_id in set(event_ids)}


async def _member(workspace_id, role):
    """A new member of `workspace_id` with a working password, as a real invitee.

    `actor()` mints a member without a usable credential, and the fixture's
    registered owners keep their signup workspace; both facts make them wrong here.
    `tenant_users` holds one active web row per person in this fixture, so this
    invitee's only workspace is the one the assertions name.
    """
    return await _invitee("NotifyMember", role, workspace_id)


@pytest.mark.parametrize("level", ["viewer", "manager", "owner", "admin"])
async def test_real_shell_roles_and_workspace_snapshot(attention, level):
    # The bell is the personal inbox: it lists the caller's own receipts and
    # resolves each destination against *current* grants. `notification UPDATE`
    # (read_id) is owner/admin-only, and a role without `collection.source VIEW`
    # cannot reach the source, so the entry is dropped. Workspace task signals are
    # an `AttentionFeed` projection, not a receipt (tests/unit_web/test_attention_feed.py).
    user = await _member(attention[0][1][0], WorkspaceRoleType(level))
    attention[2](user)
    data, event, own, foreign, wrong, deleted = await _publish(attention)
    _, workspaces, _ = data
    async with await _client() as client:
        await _login(client, user.username)
        page = await client.get("/app/")
        assert page.status_code == 200 and page.text.count("<h1") == 1
        assert "notificationsBell(" in page.text
        assert ("data-invite-open" in page.text) == (level in {"owner", "admin"})
        payload = await snapshot(client, params={"tenant_id": workspaces[1]})
        assert payload["context"] == {"workspace_id": workspaces[0], "user_id": user.id}
        assert not {foreign.id, wrong.id, deleted.id} & event_ids(payload)
        assert payload["task_events_authorized"] is False
        assert own.id in event_ids(payload)
        source_item = next(item for item in payload["notifications"] if item.get("id") == own.id)
        # A personal receipt is always the caller's to read; the workspace
        # `notification UPDATE` gate lives in `AttentionFeed`, not in this bell.
        assert source_item["read_id"] == own.id
        limited = await snapshot(client, params={"limit": 1})
        assert limited["count"] == 1 and limited["has_more"] == (payload["count"] > 1)
        response = await client.post(
            f"/app/notifications/read/{own.id}",
            data={"_csrf": await _csrf(client, "/app/"), "workspace_id": str(workspaces[0])},
        )
        # Every member who can see the receipt can mark it read.
        assert response.status_code == 200 and response.json()["ok"] is True


async def test_read_actions_share_visibility_and_do_not_resolve_failed_task(attention):
    user = await _member(attention[0][1][0], WorkspaceRoleType.ADMIN)
    attention[2](user)
    data, event, own, foreign, wrong, deleted = await _publish(attention)
    users, workspaces, _ = data
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
        # A failed task's event is an ordinary receipt here; it never resolves the
        # task into a signal in the personal bell and adds no task identity.
        item = next(item for item in before["notifications"] if item.get("id") == failure.id)
        assert item["kind"] == "notification" and "task_id" not in item
        for row in (foreign, wrong, deleted):
            response = await client.post(f"/app/notifications/read/{row.id}", data=form)
            assert response.status_code == 200 and response.json()["ok"] is False
        for _ in range(2):
            response = await client.post(f"/app/notifications/read/{own.id}", data=form)
            assert response.status_code == 200 and response.json()["ok"] is True
        response = await client.post("/app/notifications/read-all", data=form)
        # `attention` stays in one workspace, so read-all marks this caller's
        # remaining unread receipts and leaves the foreign rows alone (`own` was
        # already read by the idempotent POSTs above).
        assert response.status_code == 200 and response.json()["marked"] == 1
        after = await snapshot(client)
        assert not event_ids(after)
    assert await _receipt_state(user, (own.id, failure.id)) == {own.id: True, failure.id: True}
    # The cross-workspace rows stay unread: only explicit recipients own receipts.
    assert await _receipt_state(users[1], (foreign.id,)) == {foreign.id: False}
    assert await _receipt_state(users[0], (wrong.id, deleted.id)) == {}
    with tenant_scope(bypass=True), operator_permission_scope():
        assert (await AgentTask.objects.get(id=task.id)).last_status == "failed"


async def test_partial_feed_reports_fresh_task_identity_and_recovery(attention, monkeypatch):
    user = await _member(attention[0][1][0], WorkspaceRoleType.ADMIN)
    attention[2](user)
    data, create_event, own, foreign, wrong, deleted = await _publish(attention)
    _, workspaces, _ = data
    with tenant_scope(bypass=True), operator_permission_scope():
        task = await AgentTask.objects.filter(tenant_id=workspaces[0]).first()
        await AgentTask.objects.update_by_id(
            task.id, last_status="failed", last_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    row = await create_event(workspaces[0], "task", task.id, "API_ERROR")
    # The bell's own projections (connections, deliveries) are the ones that can be
    # incomplete; a failing one is reported per name and never silently dropped.
    monkeypatch.setattr("app.services.social.connections.statuses_needing_action", _outage)
    async with await _client() as client:
        await _login(client, user.username)
        payload = (await client.get("/app/notifications")).json()
        assert payload["complete"] is False and payload["unavailable"] == ["connections"]
        assert payload["count"] == len(payload["notifications"])
        assert row.id in {item.get("id") for item in payload["notifications"]}
        assert all(item["kind"] != "connection" for item in payload["notifications"])
    monkeypatch.undo()
    async with await _client() as client:
        await _login(client, user.username)
        recovered = await snapshot(client)
        assert row.id in event_ids(recovered)


async def _outage(*args, **kwargs):
    raise RuntimeError("fixture outage")


async def test_stale_context_and_csrf_reject_writes(attention):
    # A member of workspace 0 with a working password: the fixture's registered
    # owner signed up into its own workspace, which stays that caller's active
    # one, so only this member's snapshot may be asserted here.
    user = await _member(attention[0][1][0], WorkspaceRoleType.MANAGER)
    attention[2](user)
    data, _, own, *_ = await _publish(attention)
    users, workspaces, _ = data
    async with await _client() as client:
        await _login(client, user.username)
        token = await _csrf(client, "/app/")
        # The active workspace is server-owned (`tenant_users`), so a posted or
        # header workspace can never widen the read: the shipped shell sends its
        # own `X-App-Workspace` and a foreign `?tenant_id` is answered with the
        # caller's own snapshot. Only a stale *user* header is a context mismatch
        # and must reset the shell.
        stale = str(workspaces[1])
        response = await client.get(
            "/app/notifications", params={"tenant_id": stale}, headers={"X-App-Workspace": str(workspaces[0])}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["context"] == {"workspace_id": workspaces[0], "user_id": user.id}
        assert not {item["workspace_id"] for item in payload["notifications"]} - {workspaces[0]}
        response = await client.get("/app/notifications", params={"tenant_id": stale})
        assert response.status_code == 200
        assert response.json()["context"] == {"workspace_id": workspaces[0], "user_id": user.id}
        # A header naming another user is not a reset signal: the shell's own user
        # header must match the session, so a mismatch is refused outright.
        response = await client.get("/app/notifications", headers={"X-App-User": str(users[1].id)})
        assert response.status_code == 401 and response.json()["reset"] is True
        for form, headers in (
            ({"workspace_id": str(workspaces[0])}, {}),
            ({"_csrf": "not-the-session-token", "workspace_id": str(workspaces[0])}, {}),
            ({"_csrf": token, "workspace_id": str(workspaces[0])}, {"X-App-User": str(users[1].id)}),
        ):
            response = await client.post(f"/app/notifications/read/{own.id}", data=form, headers=headers)
            assert response.status_code == 403
        # A posted `workspace_id` is not an authorization signal: the target
        # workspace is server-owned, so a foreign one is simply ignored.
        response = await client.post(
            f"/app/notifications/read/{own.id}", data={"_csrf": token, "workspace_id": str(workspaces[1])}
        )
        assert response.status_code == 200 and response.json()["ok"] is True
    assert await _receipt_state(user, (own.id,)) == {own.id: True}


async def test_role_and_membership_revocation_are_checked_on_each_request(attention):
    user = await _member(attention[0][1][0], WorkspaceRoleType.VIEWER)
    attention[2](user)
    data, _, own, *_ = await _publish(attention)
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
        # read-all closes the caller's own unread window, so it needs no workspace
        # role: without one the receipt is out of reach and nothing is marked.
        response = await client.post("/app/notifications/read-all", data=form)
        assert response.status_code == 200 and response.json()["marked"] == 0
        await TenantUserManager().update_by_id(membership.id, is_active=False)
        # The bell is exempt from the no-workspace redirect, so a revoked
        # membership leaves an empty personal window instead of an invite page.
        for method, path in (
            ("GET", "/app/notifications"),
            ("POST", f"/app/notifications/read/{own.id}"),
            ("POST", "/app/notifications/read-all"),
        ):
            response = await client.request(method, path, data=form if method == "POST" else None)
            assert response.status_code == 200 and response.url.path.startswith("/app/notifications")
        payload = await snapshot(client)
        assert not event_ids(payload) and payload["context"]["user_id"] == user.id
    assert await _receipt_state(user, (own.id,)) == {own.id: False}


async def test_personal_connection_query_uses_caller_without_changing_workspace_feed(attention, monkeypatch):
    data, _, address, own, *_ = attention
    users, workspaces, _ = data
    member = await _member(workspaces[0], WorkspaceRoleType.VIEWER)
    # Both callers are addressed explicitly: each one's snapshot must carry the
    # personal receipt and ask the connection query for that same user.
    address(users[0])
    address(member)
    data, _, own, *_ = await _publish(attention)
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
