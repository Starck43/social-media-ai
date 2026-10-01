"""Tests for web notifications bell endpoints + tenant filtering UI.

Covers:
- GET /app/notifications returns unread notifications as JSON (by title presence);
- POST /app/notifications/read-all marks them read (and drops them from the list);
- POST /app/notifications/read/{id} marks a single one read;
- the notifications bell modal (base.html) is present and the dashboard block is gone;
- /app/sources page renders for a regular user with no tenant dropdown.

Note: the suite runs with `is_bypass` monkeypatched to True (see conftest), so
cross-tenant isolation is exercised by the framework's tenancy tests, not these
UI tests — these verify the endpoint wiring and rendering.
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Notification, User
from app.models.managers.tenant_manager import TenantUserManager

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


@pytest.fixture
async def client():
    async with await _client() as c:
        yield c


async def _register(client: AsyncClient) -> int:
    """Register a fresh user via the UI and return their tenant id."""
    page = await client.get("/app/register")
    csrf = CSRF_RE.search(page.text).group(1)
    username = _name("notifuser")
    resp = await client.post(
        "/app/register",
        data={"username": username, "email": f"{username}@example.com", "password": "password123", "_csrf": csrf},
    )
    assert resp.status_code == 200
    with tenant_scope(bypass=True):
        user = await User.objects.get(username=username)
        memberships = await TenantUserManager().web_memberships(user.id)
        return memberships[0].tenant_id


@pytest.fixture
async def logged_in(client):
    """A registered (authenticated) user's tenant id."""
    return await _register(client)


async def test_notifications_list_and_read_all(client: AsyncClient, logged_in: int) -> None:
    tenant_id = logged_in
    with tenant_scope(tenant_id):
        n1 = await Notification.objects.create_notification(
            title=_name("n1"), message="hello", notification_type="REPORT_READY"
        )
        n2 = await Notification.objects.create_notification(
            title=_name("n2"), message="world", notification_type="API_ERROR"
        )

    resp = await client.get("/app/notifications")
    assert resp.status_code == 200
    titles = [n["title"] for n in resp.json()["notifications"]]
    assert n1.title in titles and n2.title in titles

    read = await client.post("/app/notifications/read-all")
    assert read.status_code == 200

    # Read notifications drop out of the list entirely (no longer resurface).
    after = await client.get("/app/notifications")
    assert n1.title not in [n["title"] for n in after.json()["notifications"]]
    assert n2.title not in [n["title"] for n in after.json()["notifications"]]

    with tenant_scope(bypass=True):
        await Notification.objects.delete_by_id(n1.id)
        await Notification.objects.delete_by_id(n2.id)


async def test_notification_read_one_marks_single(client: AsyncClient, logged_in: int) -> None:
    tenant_id = logged_in
    with tenant_scope(tenant_id):
        n1 = await Notification.objects.create_notification(
            title=_name("n1"), message="hello", notification_type="REPORT_READY"
        )
        n2 = await Notification.objects.create_notification(
            title=_name("n2"), message="world", notification_type="REPORT_READY"
        )

    resp = await client.post(f"/app/notifications/read/{n1.id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    after = await client.get("/app/notifications")
    titles = [n["title"] for n in after.json()["notifications"]]
    assert n1.title not in titles
    assert n2.title in titles

    with tenant_scope(bypass=True):
        await Notification.objects.delete_by_id(n1.id)
        await Notification.objects.delete_by_id(n2.id)


async def test_notifications_bell_modal_in_base(client: AsyncClient, logged_in: int) -> None:
    page = await client.get("/app/")
    assert page.status_code == 200
    # Bell modal component present and the endpoint wired in; the dashboard's
    # standalone notifications block is gone.
    assert "notificationsBell()" in page.text
    assert "/app/notifications" in page.text
    assert "Уведомления" in page.text
    assert "Нет новых уведомлений" not in page.text


async def test_sources_page_renders_for_regular_user(client: AsyncClient, logged_in: int) -> None:
    page = await client.get("/app/sources")
    assert page.status_code == 200
    assert "Все тенанты" not in page.text
    assert "Добавить источник" in page.text
