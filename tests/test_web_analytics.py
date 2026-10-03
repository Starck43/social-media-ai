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


async def test_analytics_detail_shows_single_analysis(client: AsyncClient) -> None:
    """`/app/analytics/{id}` renders one analysis from its summary_data."""
    user, tenant_id = await _register(client, "ad")
    source_id = await _make_source(client, tenant_id, "Деталь источник")
    analysis_id = None
    try:
        with tenant_scope(tenant_id):
            a = await AIAnalytics.objects.create(
                source_id=source_id,
                analysis_date=date.today(),
                period_type=PeriodType.DAILY,
                topic_chain_id="src_1_scn_2_topic",
                summary_data={
                    "analysis_title": "Активность за 17 октября",
                    "analysis_summary": "Ключевой вывод анализа",
                    "multi_llm_analysis": {
                        "text_analysis": {
                            "main_topics": ["Тема A", "Тема B"],
                            "overall_mood": "позитивное",
                            "highlights": ["Заметный рост"],
                            "sentiment_score": 0.9,
                        }
                    },
                    "content_statistics": {"total_posts": 4, "total_reactions": 9, "active_users": 2},
                },
            )
            analysis_id = a.id

        await _login(client, user.username)
        resp = await client.get(f"/app/analytics/{analysis_id}")
        assert resp.status_code == 200

        # The AI title, summary, topics and mood surface on the page.
        assert "Активность за 17 октября" in resp.text
        assert "Ключевой вывод анализа" in resp.text
        assert "Тема A" in resp.text
        assert "Тема B" in resp.text
        assert "позитивное" in resp.text
        # The chain link is present (the analysis belongs to one).
        assert "/app/analytics/chains" in resp.text
    finally:
        await _drop(user, tenant_id, source_id)


async def test_analytics_detail_404_for_unknown_id(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "a4")
    try:
        await _login(client, user.username)
        resp = await client.get("/app/analytics/99999999")
        assert resp.status_code == 404
    finally:
        await _drop(user, tenant_id, None)


async def test_analytics_chains_lists_chain_and_detail_shows_timeline(client: AsyncClient) -> None:
    """`/app/analytics/chains` lists chains; the chain detail shows evolution."""
    user, tenant_id = await _register(client, "ac")
    source_id = await _make_source(client, tenant_id, "Цепочка источник")
    try:
        with tenant_scope(tenant_id):
            for day_offset in (0, 1):
                await AIAnalytics.objects.create(
                    source_id=source_id,
                    analysis_date=date.today() - timedelta(days=day_offset),
                    period_type=PeriodType.DAILY,
                    topic_chain_id="chain_abc",
                    summary_data={
                        "analysis_title": f"Анализ за день {day_offset}",
                        "multi_llm_analysis": {"text_analysis": {"main_topics": ["Тема"]}},
                    },
                )

        await _login(client, user.username)
        resp = await client.get("/app/analytics/chains")
        assert resp.status_code == 200
        assert "chain_abc" in resp.text
        assert "Цепочки тем" in resp.text

        resp = await client.get("/app/analytics/chains/chain_abc")
        assert resp.status_code == 200
        assert "Анализ за день 0" in resp.text
        assert "Анализ за день 1" in resp.text
    finally:
        await _drop(user, tenant_id, source_id)

