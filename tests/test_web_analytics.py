"""Tests for the `/app/analytics` page (docs/design/ui.md §4.4).

Read-only aggregation over stored `ai_analytics`: verifies the route renders for
an authenticated member and that the real per-day activity + cost data (written
by the analyzer) surfaces in the page instead of being empty.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AIAnalytics, Platform, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import PeriodType, SourceType

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


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _login(client: AsyncClient, username: str, password: str = "secret-password-1") -> None:
    token = await _csrf(client, "/app/login")
    resp = await client.post("/app/login", data={"username": username, "password": password, "_csrf": token})
    assert resp.status_code == 200


async def _make_source(client: AsyncClient, tenant_id: int, name: str) -> int:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None
    external_id = secrets.token_hex(6)
    with tenant_scope(tenant_id):
        source = await Source.objects.create(
            platform_id=platform.id,
            name=name,
            source_type=SourceType.USER,
            external_id=external_id,
            is_active=True,
        )
    return source.id


async def _drop(user: User | None, tenant_id: int | None, source_id: int | None) -> None:
    if source_id is not None and tenant_id is not None:
        with tenant_scope(tenant_id):
            await Source.objects.delete(id=source_id)
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


async def test_analytics_page_shows_real_data(client: AsyncClient) -> None:
    """The page renders KPIs, activity and cost from stored content_statistics."""
    user, tenant_id = await _register(client, "an")
    source_id = await _make_source(client, tenant_id, "Аналитика источник")
    try:
        with tenant_scope(tenant_id):
            await AIAnalytics.objects.create(
                source_id=source_id,
                analysis_date=date.today(),
                period_type=PeriodType.DAILY,
                summary_data={
                    "content_statistics": {
                        "total_posts": 4,
                        "messages_count": 4,
                        "active_users": 2,
                        "total_reactions": 9,
                        "total_comments": 3,
                        "total_views": 120,
                    }
                },
                request_tokens=100,
                response_tokens=50,
                estimated_cost=25,  # USD cents
            )
            await AIAnalytics.objects.create(
                source_id=source_id,
                analysis_date=date.today() - timedelta(days=1),
                period_type=PeriodType.DAILY,
                summary_data={
                    "content_statistics": {
                        "total_posts": 2,
                        "messages_count": 2,
                        "active_users": 1,
                        "total_reactions": 4,
                        "total_comments": 1,
                        "total_views": 60,
                    }
                },
                request_tokens=80,
                response_tokens=40,
                estimated_cost=15,
            )

        await _login(client, user.username)
        resp = await client.get("/app/analytics")
        assert resp.status_code == 200

        # KPIs: 6 posts, peak 2 active users, $0.40 total LLM cost.
        assert "6" in resp.text
        assert "2" in resp.text
        assert "$0.40" in resp.text

        # Activity trend rows surface the per-day user counts.
        assert "актив" in resp.text or "юз." in resp.text
    finally:
        await _drop(user, tenant_id, source_id)
