"""Tests for web notifications bell endpoints + tenant filtering UI.

Covers:
- GET /app/notifications returns typed, redacted unread events;
- POST /app/notifications/read-all marks them read (and drops them from the list);
- POST /app/notifications/read/{id} marks a single one read;
- the notifications bell modal (base.html) is present and the dashboard block is gone;
- /app/sources page renders for a regular user with no tenant dropdown.

Uses real membership permissions and owned data on the approved existing target,
without conftest bootstrap. Cross-workspace cases live in the acceptance module.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path
from unittest.mock import AsyncMock

# isort: off
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from httpx import ASGITransport, AsyncClient

# Resolve the approved target before any application imports.
from tests.web.test_merged_home_acceptance import actor, existing_target, home
from tests.notifications.test_merged_notifications_acceptance import notification_target
from tests.web.test_web_settings import _login

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Notification, NotificationReceipt
from app.services.notifications.service import notify

# isort: on

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
pytestmark = pytest.mark.tenancy


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


@pytest.fixture
async def client():
    async with await _client() as c:
        yield c


@pytest.fixture
async def logged_in(client, home):
    """Return the real member's id; the inbox is receipt-owned, so address that user."""
    user = await actor(home, "manager")
    await _login(client, user.username)
    return user.id


@pytest.fixture(autouse=True)
async def owned_notifications(home, logged_in):
    """Drop only the fixture's own workspace rows; the bell counts unread ones.

    `notification_receipts` has no workspace column (its event carries it), so the
    receipts go first via the event ids and the user id they were published for.
    """
    tenant_id = home[1][0]
    try:
        yield
    finally:
        with tenant_scope(tenant_id), operator_permission_scope():
            events = await Notification.objects.filter(tenant_id=tenant_id)
            for event_id in [event.id for event in events]:
                receipts = await NotificationReceipt.objects.filter(notification_id=event_id, user_id=logged_in)
                for receipt in receipts:
                    await NotificationReceipt.objects.delete_by_id(receipt.id)
                await Notification.objects.delete_by_id(event_id)


async def _notify(title: str, message: str, notification_type: str, recipient_ids: list[int]) -> Notification:
    """The production producer: an event plus personal receipts, in the current workspace."""
    return await notify.create(title, message, notification_type, recipient_user_ids=recipient_ids)


@pytest.fixture(autouse=True)
def no_deployment_connection_notice(monkeypatch):
    """The bell also lists personal connection notices, which depend on deployment.

    A registered VK app makes every account without a token "needs a connection",
    and an unconfigured Telegram session counts the same way. Those items are not
    notifications, so pin the connection projection to empty and assert events only.
    """
    monkeypatch.setattr("app.services.social.connections.statuses_needing_action", AsyncMock(return_value=[]))


async def test_notifications_list_and_read_all(client: AsyncClient, logged_in: int, home) -> None:
    tenant_id = home[1][0]
    with tenant_scope(tenant_id), operator_permission_scope():
        n1 = await _notify(_name("n1"), "hello", "REPORT_READY", [logged_in])
        n2 = await _notify(_name("n2"), "world", "API_ERROR", [logged_in])

    resp = await client.get("/app/notifications")
    assert resp.status_code == 200
    items = resp.json()["notifications"]
    assert {n1.id, n2.id} <= {n.get("id") for n in items}
    # The projection returns stored titles verbatim; only the producer redacts.
    titles = {n["id"]: n["title"] for n in items}
    assert (titles[n1.id], titles[n2.id]) == (n1.title, n2.title)

    page = await client.get("/app/")
    csrf = CSRF_RE.search(page.text).group(1)
    read = await client.post("/app/notifications/read-all", data={"_csrf": csrf, "workspace_id": str(tenant_id)})
    assert read.status_code == 200

    # Read notifications drop out of the list entirely (no longer resurface).
    after = await client.get("/app/notifications")
    assert not {n1.id, n2.id} & {n.get("id") for n in after.json()["notifications"]}


async def test_notification_read_one_marks_single(client: AsyncClient, logged_in: int, home) -> None:
    tenant_id = home[1][0]
    with tenant_scope(tenant_id), operator_permission_scope():
        n1 = await _notify(_name("n1"), "hello", "REPORT_READY", [logged_in])
        n2 = await _notify(_name("n2"), "world", "REPORT_READY", [logged_in])

    page = await client.get("/app/")
    csrf = CSRF_RE.search(page.text).group(1)
    rejected = await client.post(f"/app/notifications/read/{n1.id}")
    assert rejected.status_code == 403
    resp = await client.post(f"/app/notifications/read/{n1.id}", data={"_csrf": csrf, "workspace_id": str(tenant_id)})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    after = await client.get("/app/notifications")
    ids = {n.get("id") for n in after.json()["notifications"]}
    assert n1.id not in ids and n2.id in ids


async def test_notifications_bell_modal_in_base(client: AsyncClient, logged_in: int) -> None:
    page = await client.get("/app/")
    assert page.status_code == 200
    # Bell modal component present and the endpoint wired in; the dashboard's
    # standalone notifications block is gone.
    assert "notificationsBell(" in page.text
    assert "js/shell.js" in page.text
    assert "Уведомления" in page.text
    assert "Нет новых уведомлений" not in page.text


async def test_sources_page_renders_for_regular_user(client: AsyncClient, logged_in: int) -> None:
    page = await client.get("/app/sources")
    assert page.status_code == 200
    assert "Все тенанты" not in page.text
    assert "Добавить источник" in page.text
