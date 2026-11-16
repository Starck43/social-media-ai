"""Real middleware + ASGI, no dependency overrides or provider calls."""

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from app.core.config import settings
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, NotificationReceipt, TenantUser
from app.types import NotificationType
from tests.shared.fixtures.personal_workspace import personal_world

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]

from tests.shared.web_session import access_token as token
from tests.shared.web_session import session_cookie as cookie


async def test_personal_api_aliases_filters_stats_and_independent_reads(personal_world):
    w = personal_world
    app = create_application()
    events = []
    for index in (0, 1):
        with tenant_scope(w.tenants[index].id):
            events.append(
                await NotificationReceipt.objects.publish(
                    recipient_user_ids=[w.users[0].id],
                    title="mine",
                    message="private",
                    notification_type=NotificationType.REPORT_READY,
                )
            )
    with tenant_scope(w.tenants[0].id):
        foreign = await NotificationReceipt.objects.publish(
            recipient_user_ids=[w.users[1].id],
            title="foreign",
            message="not mine",
            notification_type=NotificationType.API_ERROR,
        )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver.invalid",
        headers={"Authorization": "Bearer " + token(w.users[0])},
    ) as client:
        for root in ("/api/v1/notifications", "/api/v1/notifications/notifications"):
            response = await client.get(root, params={"notification_type": "report_ready"})
            assert response.status_code == 200, response.text
            assert {n["id"] for n in response.json()} == {e.id for e in events}
            assert (await client.get(root + "/stats")).json() == {
                "total": 2,
                "unread": 2,
                "by_type": {"REPORT_READY": 2},
            }
            assert (await client.get(root + f"/{foreign.id}")).status_code == 404
        response = await client.post(f"/api/v1/notifications/{events[0].id}/mark-read")
        assert response.status_code == 200
        assert (await client.get("/api/v1/notifications/stats")).json()["unread"] == 1
        assert (await client.post(f"/api/v1/notifications/{foreign.id}/mark-read")).status_code == 404
        assert (await client.delete(f"/api/v1/notifications/{events[1].id}")).status_code == 200
        assert (await client.get("/api/v1/notifications", params={"notification_type": "unknown"})).status_code == 422


async def test_web_open_switches_only_after_csrf_and_fresh_ownership_check(personal_world):
    w = personal_world
    app = create_application()
    with tenant_scope(w.tenants[1].id), operator_permission_scope():
        task = await AgentTask.objects.create(name=w.tag, cron_expr="0 0 * * *", job_type="collect")
        event = await NotificationReceipt.objects.publish(
            recipient_user_ids=[w.users[0].id],
            title="invited",
            message="safe",
            notification_type=NotificationType.REPORT_READY,
            related_entity_type="task",
            related_entity_id=task.id,
        )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver.invalid", follow_redirects=False
    ) as client:
        client.cookies.set("session", cookie(w.users[0], w.tenants[0].id), domain="testserver.invalid")
        response = await client.get("/app/notifications")
        assert response.status_code == 200, response.text
        assert (
            next(n for n in response.json()["notifications"] if n["kind"] == "notification")["workspace_id"]
            == w.tenants[1].id
        )
        assert (await client.get("/app/notifications/inbox")).status_code == 200
        assert (await client.get(f"/app/notifications/open/{event.id}")).status_code == 200
        session = json.loads(
            base64.b64decode(TimestampSigner(settings.SECRET_KEY).unsign(client.cookies.get("session")))
        )
        assert session["app_tenant_id"] == w.tenants[0].id
        assert (await client.post(f"/app/notifications/open/{event.id}", data={"_csrf": "bad"})).status_code == 403
        csrf = app.state.csrf_manager.generate_token()
        response = await client.post(
            f"/app/notifications/open/{event.id}",
            data={"_csrf": csrf, "tenant_id": w.tenants[0].id, "next": "https://evil.invalid"},
        )
        assert response.status_code == 303 and response.headers["location"] == f"/app/tasks/{task.id}"
        session = json.loads(
            base64.b64decode(TimestampSigner(settings.SECRET_KEY).unsign(client.cookies.get("session")))
        )
        assert session["app_tenant_id"] == w.tenants[1].id
        with tenant_scope(w.tenants[1].id):
            await TenantUser.objects.update_by_id(w.memberships[1].id, is_active=False)
        assert (await client.post(f"/app/notifications/open/{event.id}", data={"_csrf": csrf})).status_code == 404


async def test_another_user_and_no_membership_cannot_open_or_read(personal_world):
    w = personal_world
    app = create_application()
    with tenant_scope(w.tenants[0].id):
        event = await NotificationReceipt.objects.publish(
            recipient_user_ids=[w.users[0].id],
            title="mine",
            message="safe",
            notification_type=NotificationType.REPORT_READY,
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver.invalid") as client:
        for user in (w.users[1], w.users[3]):
            client.cookies.clear()
            client.cookies.set("session", cookie(user, w.tenants[0].id), domain="testserver.invalid")
            response = await client.get("/app/notifications")
            assert response.status_code == 200, response.text
            assert not [n for n in response.json()["notifications"] if n["kind"] == "notification"]
            assert (
                await client.post(
                    f"/app/notifications/open/{event.id}", data={"_csrf": app.state.csrf_manager.generate_token()}
                )
            ).status_code == 404
            assert not (
                await client.post(
                    f"/app/notifications/read/{event.id}", data={"_csrf": app.state.csrf_manager.generate_token()}
                )
            ).json()["ok"]


async def test_signed_navigation_is_user_bound_and_not_an_acknowledgement(personal_world):
    from app.services.notifications.links import open_link

    w = personal_world
    app = create_application()
    with tenant_scope(w.tenants[1].id):
        event = await NotificationReceipt.objects.publish(
            recipient_user_ids=[w.users[0].id, w.users[2].id],
            title="signed",
            message="safe",
            notification_type=NotificationType.REPORT_READY,
        )
    link = open_link(event.id, w.users[0].id)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver.invalid", follow_redirects=False
    ) as client:
        client.cookies.set("session", cookie(w.users[0], w.tenants[0].id), domain="testserver.invalid")
        assert (await client.get(f"/app/notifications/open/{event.id}?intent=invalid")).status_code == 403
        response = await client.get(link)
        assert response.status_code == 303 and response.headers["location"] == "/app/"
        session = json.loads(
            base64.b64decode(TimestampSigner(settings.SECRET_KEY).unsign(client.cookies.get("session")))
        )
        assert session["app_tenant_id"] == w.tenants[1].id
        snapshot = (await client.get("/app/notifications")).json()
        assert any(n.get("id") == event.id for n in snapshot["notifications"])
        client.cookies.clear()
        client.cookies.set("session", cookie(w.users[2], w.tenants[1].id), domain="testserver.invalid")
        assert (await client.get(link)).status_code == 403
