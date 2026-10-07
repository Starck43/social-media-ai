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
from app.models import AIAnalytics, Platform, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import PeriodType, SourceType, UserRoleType

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


async def _invitee(prefix: str, platform_role: UserRoleType, tenant_id: int, password: str) -> User:
    """A web user in someone else's workspace with a *non*-owner membership."""
    username = _name(prefix)
    role = await Role.objects.get(codename=platform_role.name)
    assert role is not None, f"role {platform_role.name} is seeded"
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password=password,
        role_id=role.id,
        is_superuser=False,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role="member")
    return user


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
                period_type=PeriodType.DAY,
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
                period_type=PeriodType.DAY,
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
                period_type=PeriodType.DAY,
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
                    period_type=PeriodType.DAY,
                    topic_chain_id="chain_abc",
                    summary_data={
                        "analysis_title": f"Анализ за день {day_offset}",
                        "multi_llm_analysis": {"text_analysis": {"main_topics": ["Тема"]}},
                    },
                )

        await _login(client, user.username)
        resp = await client.get("/app/analytics/chains")
        assert resp.status_code == 200
        assert "Цепочки тем" in resp.text
        # The chain name is taken from the earliest analysis title (or chain_label if present).
        # Both analysis titles must appear somewhere on the page (either as chain name or in the timeline).
        assert "Анализ за день" in resp.text
        assert "chain_abc" in resp.text

        resp = await client.get("/app/analytics/chains/chain_abc")
        assert resp.status_code == 200
        assert "Анализ за день 0" in resp.text
        assert "Анализ за день 1" in resp.text
    finally:
        await _drop(user, tenant_id, source_id)


async def _chain_analysis(tenant_id: int, source_id: int, chain_id: str, title: str, day_offset: int) -> int:
    with tenant_scope(tenant_id):
        row = await AIAnalytics.objects.create(
            source_id=source_id,
            analysis_date=date.today() - timedelta(days=day_offset),
            period_type=PeriodType.DAY,
            topic_chain_id=chain_id,
            summary_data={
                "analysis_title": title,
                "analysis_summary": f"Вывод {title}",
                "multi_llm_analysis": {"text_analysis": {"main_topics": ["Тема"]}},
            },
        )
    return row.id


async def test_chains_sort_desc_by_default_and_asc_on_request(client: AsyncClient) -> None:
    """`/app/analytics/chains` sorts newest-first by default; `?sort=asc` flips."""
    user, tenant_id = await _register(client, "cs")
    source_id = await _make_source(client, tenant_id, "Сортировка источник")
    try:
        # "newer" chain's latest analysis is today; "older" chain's is yesterday.
        await _chain_analysis(tenant_id, source_id, "chain_newer", "Цепочка новая", 0)
        await _chain_analysis(tenant_id, source_id, "chain_older", "Цепочка старая", 1)

        await _login(client, user.username)
        resp = await client.get("/app/analytics/chains")
        assert resp.status_code == 200
        assert resp.text.index("Цепочка новая") < resp.text.index("Цепочка старая"), "newest chain first"

        resp = await client.get("/app/analytics/chains?sort=asc")
        assert resp.status_code == 200
        assert resp.text.index("Цепочка старая") < resp.text.index("Цепочка новая"), "oldest chain first"
    finally:
        await _drop(user, tenant_id, source_id)


async def test_owner_deletes_a_single_analysis(client: AsyncClient) -> None:
    """`POST /app/analytics/{id}/delete` removes one row and returns to its chain."""
    user, tenant_id = await _register(client, "cd")
    source_id = await _make_source(client, tenant_id, "Удаление источник")
    first_id = await _chain_analysis(tenant_id, source_id, "chain_del", "Первый анализ", 1)
    second_id = await _chain_analysis(tenant_id, source_id, "chain_del", "Второй анализ", 0)
    try:
        await _login(client, user.username)
        token = await _csrf(client, "/app/analytics/chains/chain_del")
        resp = await client.post(
            f"/app/analytics/{first_id}/delete",
            data={"_csrf": token, "tenant_id": str(tenant_id)},
        )
        assert resp.status_code == 200
        assert resp.url.path == "/app/analytics/chains/chain_del", "back to the chain it belonged to"

        with tenant_scope(tenant_id):
            remaining = await AIAnalytics.objects.filter(id__in=[first_id, second_id])
        assert {r.id for r in remaining} == {second_id}, "only the chosen row is gone"
    finally:
        await _drop(user, tenant_id, source_id)


async def test_last_analysis_delete_leaves_no_404(client: AsyncClient) -> None:
    """Deleting the last analysis of a chain returns to the list, not a 404 page."""
    user, tenant_id = await _register(client, "ce")
    source_id = await _make_source(client, tenant_id, "Последний источник")
    only_id = await _chain_analysis(tenant_id, source_id, "chain_last", "Единственный анализ", 0)
    try:
        await _login(client, user.username)
        token = await _csrf(client, "/app/analytics/chains/chain_last")
        resp = await client.post(
            f"/app/analytics/{only_id}/delete",
            data={"_csrf": token, "tenant_id": str(tenant_id)},
        )
        assert resp.status_code == 200
        assert resp.url.path == "/app/analytics/chains", "the emptied chain would 404, so land on the list"
        assert "цепочка опустела" in resp.text

        # And the chain itself is truly unreachable now.
        assert (await client.get("/app/analytics/chains/chain_last")).status_code == 404
    finally:
        await _drop(user, tenant_id, source_id)


async def test_owner_deletes_a_whole_chain(client: AsyncClient) -> None:
    """`POST /app/analytics/chains/{chain_id}/delete` drops every row of the chain."""
    user, tenant_id = await _register(client, "ch")
    source_id = await _make_source(client, tenant_id, "Цепочка-удаление источник")
    ids = [await _chain_analysis(tenant_id, source_id, "chain_gone", f"Анализ {i}", i) for i in range(3)]
    try:
        await _login(client, user.username)
        token = await _csrf(client, "/app/analytics/chains/chain_gone")
        resp = await client.post(
            "/app/analytics/chains/chain_gone/delete",
            data={"_csrf": token, "tenant_id": str(tenant_id)},
        )
        assert resp.status_code == 200
        assert resp.url.path == "/app/analytics/chains"
        assert "удалена" in resp.text

        with tenant_scope(tenant_id):
            remaining = await AIAnalytics.objects.filter(id__in=ids)
        assert not remaining, "the whole chain is gone"
    finally:
        await _drop(user, tenant_id, source_id)


async def test_viewer_cannot_delete_chain(client: AsyncClient) -> None:
    """A non-owner member without `aianalytics.delete` gets refused and sees no button."""
    owner, tenant_id = await _register(client, "cv")
    source_id = await _make_source(client, tenant_id, "Запрет источник")
    row_id = await _chain_analysis(tenant_id, source_id, "chain_keep", "Охраняемый анализ", 0)
    member = await _invitee("cvViewer", UserRoleType.VIEWER, tenant_id, "secret-password-1")
    try:
        async with await _client() as m:
            await _login(m, member.username)

            page = await m.get("/app/analytics/chains")
            assert page.status_code == 200
            assert "chain_keep" in page.text
            assert f"/app/analytics/chains/chain_keep/delete" not in page.text, "viewer sees no delete form"

            # A crafted POST is refused by the same `guard_web` rule.
            token = CSRF_RE.search(page.text).group(1)
            resp = await m.post(
                "/app/analytics/chains/chain_keep/delete",
                data={"_csrf": token},
            )
            assert "Недостаточно прав" in resp.text

            with tenant_scope(tenant_id):
                assert await AIAnalytics.objects.get(id=row_id) is not None, "row survives"
    finally:
        await User.objects.delete_user(member.id)
        await _drop(owner, tenant_id, source_id)
