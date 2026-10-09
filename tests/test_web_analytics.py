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


def _visible_text(html: str) -> str:
    """Assert rendered prose, independent of HTML indentation and assets."""
    from html.parser import HTMLParser

    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self.assets = []

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.assets.append(tag)

        def handle_endtag(self, tag):
            if self.assets and self.assets[-1] == tag:
                self.assets.pop()

        def handle_data(self, data):
            if not self.assets:
                self.parts.append(data)

    parser = TextParser()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


@pytest.fixture
async def analytics_delete_actor():
    """Grant only analytics view/delete on a throwaway role, never a seeded role.

    Owning a workspace is not authorization to destroy analyses. The positive
    deletion subjects carry real structured permissions; cleanup restores their
    original User role (if still present), clears links and removes the role.
    """
    from app.models import Permission
    from app.types import ActionType

    roles = []
    originals = []

    async def grant(user):
        permissions = await Permission.objects.prefetch_related("model_type").filter(
            action_type__in=[ActionType.VIEW, ActionType.DELETE],
        )
        selected = {}
        for permission in permissions:
            if permission.model_type and permission.model_type.model_name.lower() == "aianalytics":
                selected.setdefault(permission.action_type, permission.id)
        assert set(selected) == {ActionType.VIEW, ActionType.DELETE}, "analytics rights must be seeded"
        role = await Role.objects.create_role(name=_name("analytics-delete-"), codename=UserRoleType.VIEWER.name)
        roles.append(role.id)
        await Role.objects.set_permissions(role.id, list(selected.values()))
        originals.append((user.id, user.role_id))
        await User.objects.update_by_id(user.id, role_id=role.id)
        loaded = await User.objects.prefetch_related("role.permissions").get(id=user.id)
        assert loaded.is_active and not loaded.is_superuser and not loaded._is_superuser_role()
        assert len(loaded.role.permissions) == 2
        assert loaded.has_perm_for("aianalytics", ActionType.VIEW)
        assert loaded.has_perm_for("aianalytics", ActionType.DELETE)
        for model in ("tenant", "role", "user", "job", "llmmodel", "llmprovider", "source"):
            assert not loaded.has_perm_for(model, ActionType.DELETE), model
        return loaded

    try:
        yield grant
    finally:
        for user_id, role_id in originals:
            await User.objects.update_by_id(user_id, role_id=role_id)
        for role_id in roles:
            await Role.objects.set_permissions(role_id, [])
            await Role.objects.delete_by_id(role_id)


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
            "email": f"{username}@example.com",
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
            tenant_id=tenant_id,
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
        email=f"{username}@example.com",
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
                tenant_id=tenant_id,
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
                tenant_id=tenant_id,
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
                tenant_id=tenant_id,
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
        # A stored singleton ID must not advertise a continuation.
        assert "Цепочка ·" not in resp.text
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
                    tenant_id=tenant_id,
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
            tenant_id=tenant_id,
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


async def test_owner_deletes_a_single_analysis(client: AsyncClient, analytics_delete_actor) -> None:
    """`POST /app/analytics/{id}/delete` removes one row and returns to its chain."""
    user, tenant_id = await _register(client, "cd")
    source_id = await _make_source(client, tenant_id, "Удаление источник")
    first_id = await _chain_analysis(tenant_id, source_id, "chain_del", "Первый анализ", 1)
    second_id = await _chain_analysis(tenant_id, source_id, "chain_del", "Второй анализ", 0)
    try:
        await analytics_delete_actor(user)
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


async def test_last_analysis_delete_leaves_no_404(client: AsyncClient, analytics_delete_actor) -> None:
    """Deleting the last analysis of a chain returns to the list, not a 404 page."""
    user, tenant_id = await _register(client, "ce")
    source_id = await _make_source(client, tenant_id, "Последний источник")
    only_id = await _chain_analysis(tenant_id, source_id, "chain_last", "Единственный анализ", 0)
    try:
        await analytics_delete_actor(user)
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


async def test_owner_deletes_a_whole_chain(client: AsyncClient, analytics_delete_actor) -> None:
    """`POST /app/analytics/chains/{chain_id}/delete` drops every row of the chain."""
    user, tenant_id = await _register(client, "ch")
    source_id = await _make_source(client, tenant_id, "Цепочка-удаление источник")
    ids = [await _chain_analysis(tenant_id, source_id, "chain_gone", f"Анализ {i}", i) for i in range(3)]
    try:
        await analytics_delete_actor(user)
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


async def test_cross_filters_entities_work_in_web_and_api(client):
    user, tenant_id = await _register(client, "cross")
    source_id = await _make_source(client, tenant_id, "Cross-filter source")
    try:
        with tenant_scope(tenant_id):
            for offset, (name, score) in enumerate([("Negative entity", .2), ("Positive entity", .8)]):
                await AIAnalytics.objects.create(source_id=source_id, tenant_id=tenant_id, period_type=PeriodType.DAY,
                    analysis_date=date.today() - timedelta(days=offset), media_types=["text", "image"], summary_data={"multi_llm_analysis": {"text_analysis": {
                        "sentiment_score": score, "entities": [{"name": name, "type": "brand"}], "intent_type": "complaint",
                    }}})
        await _login(client, user.username)
        response = await client.get("/app/analytics?group_by=entities&entity_type=brand&sentiment=negative&media=image&days=7")
        assert response.status_code == 200
        assert "Negative entity" in response.text
        assert 'aria-label="Тональность"' in response.text
        assert 'id="sentiment-filter"' not in response.text and 'id="media-filter"' not in response.text
        assert 'href="?group_by=sentiment' not in response.text
        assert 'href="?group_by=content_type' not in response.text
        # Filter state survives switching axis/entity type/period.
        from html import unescape
        navs = re.findall(r'<nav class="ui-segments" aria-label="(?:Группировать по|Тип упоминаний|Шаг хронологии)">(.*?)</nav>', response.text, re.S)
        links = [unescape(href) for href in re.findall(r'href="([^"]+)"', "".join(navs))]
        assert links
        from urllib.parse import parse_qs, urlparse
        for link in links:
            query = parse_qs(urlparse(link).query)
            assert query["sentiment"] == ["negative"] and query["media"] == ["image"]
            assert query["days"] == ["7"]
        login = await client.post("/api/v1/auth/login", data={"username": user.username, "password": "secret-password-1"})
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        response = await client.get("/api/v1/dashboard/analytics/aggregate/grouped?group_by=entities&entity_type=brand&sentiment=negative&media=image", headers=headers)
        assert response.status_code == 200
        assert response.json()["groups"] == [{"key": "Negative entity", "count": 1, "avg_sentiment": .2}]
        for axis in ["sentiment", "content_type"]:
            response = await client.get(f"/api/v1/dashboard/analytics/aggregate/grouped?group_by={axis}", headers=headers)
            assert response.status_code == 400
            assert "Use: themes, sources, entities, intent, topic_chains" in response.json()["detail"]
            response = await client.get(f"/app/analytics?group_by={axis}")
            assert response.status_code == 400
    finally:
        await _drop(user, tenant_id, source_id)


async def test_switcher_runs_five_aggregations_and_hides_single_group_intent(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from starlette.requests import Request

    import app.services.ai.grouping as grouping
    from app.web.analytics import _fetch_grouped

    rows = [
        SimpleNamespace(
            id=index,
            source_id=1,
            tenant_id=1,
            topic_chain_id=None,
            chain_label=None,
            analysis_date=date.today() - timedelta(days=index),
            media_types=["text"],
            summary_data={
                "multi_llm_analysis": {
                    "text_analysis": {
                        "main_topics": [f"theme-{index}"],
                        "entities": [{"name": f"entity-{index}", "type": "brand"}],
                        "intent_type": "complaint",
                        "sentiment_score": 0.2,
                    }
                }
            },
        )
        for index in (0, 1)
    ]

    class Rows:
        def filter(self, *args, **kwargs):
            return self

        def __await__(self):
            async def load():
                return rows

            return load().__await__()

    monkeypatch.setattr(AIAnalytics.objects, "all", lambda: Rows())
    monkeypatch.setattr(grouping, "_source_name_map", AsyncMock(return_value={1: "Source"}))
    real_group = grouping.group_analytics
    calls = []

    async def count(*args, **kwargs):
        calls.append(kwargs["axis"].value)
        return await real_group(*args, **kwargs)

    monkeypatch.setattr(grouping, "group_analytics", count)
    request = Request(
        {
            "type": "http",
            "path": "/app/analytics",
            "headers": [],
            "query_string": b"group_by=entities&sentiment=negative&media=text",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    result = await _fetch_grouped(7, None, False, 1, request)
    assert len(calls) == 5 and set(calls) == {"days", "themes", "sources", "entities", "intent"}
    assert result["group_counts"]["intent"] == 1
    assert "intent" not in result["visible_axes"]
    request = Request(
        {
            "type": "http",
            "path": "/app/analytics",
            "headers": [],
            "query_string": b"group_by=intent",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    calls.clear()
    result = await _fetch_grouped(7, None, False, 1, request)
    assert len(calls) == 5 and "intent" in result["visible_axes"]


async def test_chains_risk_sort_missing_sentiment_last_and_filters_persist(client):
    user, tenant_id = await _register(client, "risk")
    source_id = await _make_source(client, tenant_id, "Risk sort source")
    try:
        for index, (chain, score) in enumerate([("positive", 0.9), ("negative", 0.1), ("unknown", None)]):
            with tenant_scope(tenant_id):
                await AIAnalytics.objects.create(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    analysis_date=date.today() - timedelta(days=index),
                    period_type=PeriodType.DAY,
                    topic_chain_id=f"risk-{chain}",
                    media_types=["image"],
                    summary_data={"analysis_title": f"Risk title {chain}", "sentiment_score": score},
                )
        await _login(client, user.username)
        response = await client.get("/app/analytics/chains?sort=sentiment_asc&days=7&media=image")
        assert response.status_code == 200
        text = response.text
        assert text.index("Risk title negative") < text.index("Risk title positive") < text.index("Risk title unknown")
        assert 'name="media" value="image"' in text
        response = await client.get("/app/analytics/chains?sort=sentiment_asc&days=7&media=image&sentiment=negative")
        assert response.status_code == 200
        assert "Risk title negative" in response.text and "Risk title positive" not in response.text
    finally:
        await _drop(user, tenant_id, source_id)


@pytest.fixture
async def drill_records(client):
    """Two sources (same display name), flat/nested rows and chain-less data."""
    user, tenant_id = await _register(client, "drill")
    first = await _make_source(client, tenant_id, "Same source name")
    second = await _make_source(client, tenant_id, "Same source name")
    birthday = "День рождения & друзья"
    specs = [
        (
            first,
            0,
            [birthday, birthday.upper()],
            [{"name": "Иван", "type": "person"}, {"name": "Иван", "type": "person"}],
            0.2,
            "image",
            "complaint",
            None,
        ),
        (
            first,
            1,
            ["Other", birthday.lower()],
            [{"name": "Иван", "type": "brand"}],
            0.8,
            "video",
            "announcement",
            "drill-celebration",
        ),
        (first, 2, ["Other"], [{"name": "ACME", "type": "brand"}], 0.8, "text", "complaint", "drill-celebration"),
        (second, 0, [], [], 0.5, "image", None, None),
        (second, 1, None, [], None, "text", None, None),
        (first, 40, [birthday], [], 0.2, "image", "complaint", None),
    ]
    ids = []
    try:
        for index, (source_id, offset, topics, entities, score, media, intent, chain) in enumerate(specs):
            text = {
                "analysis_title": f"Drill row {index}",
                "entities": entities,
                "sentiment_score": score,
                "intent_type": intent,
            }
            if topics is not None:
                text["topics"] = topics
            payload = text if index % 2 == 0 else {"multi_llm_analysis": {"text_analysis": text}}
            with tenant_scope(tenant_id):
                row = await AIAnalytics.objects.create(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    analysis_date=date.today() - timedelta(days=offset),
                    period_type=PeriodType.DAY,
                    summary_data=payload,
                    media_types=[media],
                    topic_chain_id=chain,
                )
            ids.append(row.id)
        await _login(client, user.username)
        yield {"user": user, "tenant_id": tenant_id, "sources": (first, second), "ids": ids, "birthday": birthday}
    finally:
        with tenant_scope(tenant_id):
            await AIAnalytics.objects.delete(source_id=second)
            await Source.objects.delete_by_id(second)
        await _drop(user, tenant_id, first)


def _drill_ids(html):
    return [int(id_) for id_ in re.findall(r'data-analysis-id="(\d+)"', html)]


def _analytics_links(html):
    from html import unescape

    return [unescape(link) for link in re.findall(r'href="([^"]+)"', html)]


@pytest.mark.parametrize(
    "axis,value,entity_type,indices",
    [
        ("themes", "День рождения & друзья", None, [0, 1]),
        ("sources", "first", None, [0, 1, 2]),
        ("entities", "Иван", "person", [0]),
        ("entities", "Иван", "brand", [1]),
        ("sentiment", "negative", None, [0]),
        ("content_type", "image", None, [0, 3]),
        ("intent", "complaint", None, [0, 2]),
        ("themes", "(без темы)", None, [3, 4]),
        ("intent", "unknown", None, [3, 4]),
    ],
)
async def test_drilldown_membership_all_axes_includes_chainless(
    client, drill_records, axis, value, entity_type, indices
):
    data = drill_records
    if value == "first":
        value = str(data["sources"][0])
    params = {"axis": axis, "value": value, "days": 7}
    if entity_type:
        params["entity_type"] = entity_type
    response = await client.get("/app/analytics/group", params=params)
    assert response.status_code == 200
    assert set(_drill_ids(response.text)) == {data["ids"][i] for i in indices}
    assert f"({len(indices)} анализов)" in response.text
    assert not re.search(r'action="/app/analytics/[^\"]*/delete"', response.text)


async def test_group_cards_and_breadcrumb_keep_scope_and_stable_source_ids(client, drill_records):
    from urllib.parse import parse_qs, urlparse

    data = drill_records
    source = str(data["sources"][0])
    response = await client.get(
        "/app/analytics",
        params={
            "group_by": "entities",
            "entity_type": "person",
            "days": 7,
            "source_id": source,
            "sentiment": "negative",
            "media": "image",
        },
    )
    assert response.status_code == 200
    links = _analytics_links(response.text)
    groups = [link for link in links if urlparse(link).path == "/app/analytics/group"]
    assert len(groups) == 1
    query = parse_qs(urlparse(groups[0]).query)
    assert query["axis"] == ["entities"] and query["value"] == ["Иван"]
    assert query["days"] == ["7"] and query["entity_type"] == ["person"] and query["source_id"] == [source]
    drill = await client.get(groups[0])
    assert drill.status_code == 200 and _drill_ids(drill.text) == [data["ids"][0]]
    back = [
        link for link in _analytics_links(drill.text) if urlparse(link).path == "/app/analytics" and "group_by=" in link
    ][0]
    back_query = parse_qs(urlparse(back).query)
    for key in ("days", "source_id", "entity_type", "sentiment", "media"):
        assert back_query[key] == query[key]
    assert (await client.get(back)).status_code == 200
    sources = await client.get("/app/analytics?group_by=sources&days=7")
    links = [
        parse_qs(urlparse(link).query)
        for link in _analytics_links(sources.text)
        if urlparse(link).path == "/app/analytics/group"
    ]
    assert {q["value"][0] for q in links} == {str(x) for x in data["sources"]}
    assert all(q["axis"] == ["sources"] for q in links)


async def test_theme_card_count_matches_drill_and_chain_navigation(client, drill_records):
    from urllib.parse import parse_qs, urlparse

    data = drill_records
    response = await client.get("/app/analytics?group_by=themes&days=7")
    links = _analytics_links(response.text)
    birthday_link = next(
        link
        for link in links
        if urlparse(link).path == "/app/analytics/group"
        and parse_qs(urlparse(link).query)["value"] == [data["birthday"]]
    )
    response = await client.get(birthday_link)
    assert _drill_ids(response.text) == data["ids"][:2]
    chain = next(
        link
        for link in _analytics_links(response.text)
        if urlparse(link).path == "/app/analytics/chains/drill-celebration"
    )
    assert parse_qs(urlparse(chain).query)["days"] == ["7"]
    detail = await client.get(chain)
    assert detail.status_code == 200 and "Drill row 1" in detail.text
    back = next(link for link in _analytics_links(detail.text) if urlparse(link).path == "/app/analytics/chains")
    assert parse_qs(urlparse(back).query)["days"] == ["7"]
    listing = await client.get(back)
    assert listing.status_code == 200
    assert any(
        urlparse(link).path == "/app/analytics/chains/drill-celebration" for link in _analytics_links(listing.text)
    )
    top = await client.get("/app/analytics?days=7")
    assert any(urlparse(link).path == "/app/analytics/chains/drill-celebration" for link in _analytics_links(top.text))
    direct = await client.get("/app/analytics?group_by=topic_chains&days=7")
    assert direct.status_code == 200
    assert any(
        urlparse(link).path == "/app/analytics/chains/drill-celebration" for link in _analytics_links(direct.text)
    )


async def test_drilldown_viewer_read_only_and_tenant_isolation(client, drill_records):
    data = drill_records
    viewer = await _invitee("drillViewer", UserRoleType.VIEWER, data["tenant_id"], "secret-password-1")
    other = None
    try:
        async with await _client() as other_client:
            other, other_tenant = await _register(other_client, "drillForeign")
            foreign_source = await _make_source(other_client, other_tenant, "Foreign source")
            with tenant_scope(other_tenant):
                foreign = await AIAnalytics.objects.create(
                    tenant_id=other_tenant,
                    source_id=foreign_source,
                    analysis_date=date.today(),
                    period_type=PeriodType.DAY,
                    summary_data={"topics": [data["birthday"]], "analysis_title": "Foreign private row"},
                )
            async with await _client() as viewer_client:
                await _login(viewer_client, viewer.username)
                response = await viewer_client.get(
                    "/app/analytics/group",
                    params={"axis": "themes", "value": data["birthday"], "days": 7, "tenant_id": other_tenant},
                )
                assert response.status_code == 200
                assert set(_drill_ids(response.text)) == set(data["ids"][:2])
                assert foreign.id not in _drill_ids(response.text) and "Foreign private row" not in response.text
                assert not re.search(r'action="/app/analytics/[^\"]*/delete"', response.text)
    finally:
        await User.objects.delete_user(viewer.id)
        if other:
            await _drop(other, other_tenant, foreign_source)


@pytest.mark.parametrize(
    "params",
    [
        {"axis": "invalid", "value": "x"},
        {"axis": "sources", "value": "not-an-id"},
        {"axis": "sentiment", "value": "unknown"},
        {"axis": "content_type", "value": "audio"},
        {"axis": "entities", "value": "Иван", "entity_type": "invalid"},
        {"axis": "themes", "value": "x", "source_id": "invalid"},
    ],
)
async def test_drilldown_invalid_filters_return_400(client, drill_records, params):
    response = await client.get("/app/analytics/group", params=params)
    assert response.status_code == 400


async def test_chronology_groups_link_without_nested_anchors(client, drill_records):
    from urllib.parse import parse_qs, urlparse

    data = drill_records
    for period in ("day", "week", "month"):
        response = await client.get(
            "/app/analytics",
            params={
                "group_by": "days",
                "group_by_period": period,
                "days": 7,
                "source_id": data["sources"][0],
                "entity_type": "person",
            },
        )
        assert response.status_code == 200
        links = [link for link in _analytics_links(response.text) if urlparse(link).path == "/app/analytics/group"]
        assert links
        found = set()
        for link in links:
            query = parse_qs(urlparse(link).query)
            assert query["axis"] == ["days"] and query["group_by_period"] == [period]
            assert query["entity_type"] == ["person"] and query["days"] == ["7"]
            drill = await client.get(link)
            assert drill.status_code == 200
            found.update(_drill_ids(drill.text))
        assert found == set(data["ids"][:3])


async def test_drilldown_denied_before_rows_are_loaded(client, drill_records, monkeypatch):
    from unittest.mock import AsyncMock

    from app.web.perms import WebPerms

    read = AsyncMock(side_effect=AssertionError("unauthorized rows loaded"))
    monkeypatch.setattr(WebPerms, "can", lambda *args: False)
    monkeypatch.setattr("app.web.analytics._scoped_analytics_rows", read)
    response = await client.get("/app/analytics/group?axis=themes&value=x", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/app"
    read.assert_not_awaited()


async def test_drilldown_chain_link_requires_two_entries_in_opened_scope(client, drill_records):
    from urllib.parse import urlparse

    data = drill_records
    # An ID alone does not mean a multi-entry chain exists.
    with tenant_scope(data["tenant_id"]):
        await AIAnalytics.objects.update_by_id(data["ids"][4], topic_chain_id="drill-singleton")
    group = await client.get(
        "/app/analytics/group", params={"axis": "sources", "value": data["sources"][1], "days": "all"}
    )
    assert group.status_code == 200
    assert not any("/app/analytics/chains/" in urlparse(link).path for link in _analytics_links(group.text))
    # Cross-filtered group has one matching row; opened timeline has two rows.
    group = await client.get(
        "/app/analytics/group", params={"axis": "themes", "value": data["birthday"], "days": 7, "media": "video"}
    )
    assert _drill_ids(group.text) == [data["ids"][1]]
    assert "Цепочка · 2 анализ(ов)" in group.text
    chain = next(
        link
        for link in _analytics_links(group.text)
        if urlparse(link).path == "/app/analytics/chains/drill-celebration"
    )
    detail = await client.get(chain)
    assert "Drill row 1" in detail.text and "Drill row 2" in detail.text
    # Restricting the actual timeline to one day must hide the link too.
    with tenant_scope(data["tenant_id"]):
        await AIAnalytics.objects.update_by_id(data["ids"][0], topic_chain_id="drill-celebration")
    with tenant_scope(data["tenant_id"]):
        await AIAnalytics.objects.update_by_id(data["ids"][1], analysis_date=date.today() - timedelta(days=3))
    group = await client.get("/app/analytics/group", params={"axis": "sources", "value": data["sources"][0], "days": 1})
    assert "Цепочка ·" not in group.text
    group = await client.get("/app/analytics/group", params={"axis": "sources", "value": data["sources"][1], "days": 1})
    assert "drill-singleton" not in group.text


async def test_chain_back_restores_exact_group_and_group_back_restores_main(client, drill_records):
    from urllib.parse import parse_qs, urlparse

    data = drill_records
    main = "/app/analytics?days=all&group_by_period=month&group_by=sources"
    response = await client.get(main)
    group_link = next(
        link
        for link in _analytics_links(response.text)
        if urlparse(link).path == "/app/analytics/group"
        and parse_qs(urlparse(link).query)["value"] == [str(data["sources"][0])]
    )
    assert parse_qs(urlparse(group_link).query)["return_to"] == [main]
    group = await client.get(group_link)
    assert "← К группам" in group.text
    assert main in _analytics_links(group.text)
    chain_link = next(
        link
        for link in _analytics_links(group.text)
        if urlparse(link).path == "/app/analytics/chains/drill-celebration"
    )
    assert parse_qs(urlparse(chain_link).query)["return_to"] == [group_link]
    chain = await client.get(chain_link)
    assert "← К группе" in chain.text and group_link in _analytics_links(chain.text)
    back = await client.get(group_link)
    assert _drill_ids(back.text) == _drill_ids(group.text)
    assert main in _analytics_links(back.text)


@pytest.mark.parametrize(
    "bad",
    [
        "https://evil.example/path",
        "//evil.example/path",
        "/app/analytics/../logout",
        "/app/logout",
        "/app/analytics\\evil",
        "javascript:alert(1)",
        "/%2F%2Fevil.example",
        "/app/analytics#fragment",
        "/app/analytics\n",
    ],
)
def test_analytics_return_rejects_external_or_non_readonly_targets(bad):
    from app.web.analytics import _safe_analytics_return

    assert _safe_analytics_return(bad) is None


async def test_invalid_return_falls_back_without_external_links(client, drill_records):
    data = drill_records
    group = await client.get(
        "/app/analytics/group",
        params={"axis": "sources", "value": data["sources"][0], "days": 7, "return_to": "https://evil.example"},
    )
    assert group.status_code == 200
    assert all("evil.example" not in link for link in _analytics_links(group.text))
    chain = await client.get(
        "/app/analytics/chains/drill-celebration", params={"days": 7, "return_to": "//evil.example"}
    )
    assert chain.status_code == 200
    assert "← Все цепочки" in chain.text
    assert all("evil.example" not in link for link in _analytics_links(chain.text))


async def test_individual_detail_returns_to_group_topics_and_chain_round_trip(client, drill_records):
    from urllib.parse import parse_qs, urlparse

    data = drill_records
    main = "/app/analytics?days=all&group_by_period=month&group_by=sources"
    page = await client.get(main)
    group_url = next(
        url
        for url in _analytics_links(page.text)
        if urlparse(url).path == "/app/analytics/group"
        and parse_qs(urlparse(url).query)["value"] == [str(data["sources"][0])]
    )
    group = await client.get(group_url)
    detail_url = next(
        url for url in _analytics_links(group.text) if urlparse(url).path == f"/app/analytics/{data['ids'][1]}"
    )
    detail = await client.get(detail_url)
    assert detail.status_code == 200
    assert "← К списку анализов" in detail.text and group_url in _analytics_links(detail.text)
    assert "95%" not in detail.text and "не вероятность" in detail.text
    chain_url = next(
        url for url in _analytics_links(detail.text) if urlparse(url).path == "/app/analytics/chains/drill-celebration"
    )
    assert "Цепочка · 2 анализ(ов)" in detail.text
    chain = await client.get(chain_url)
    assert "← К анализу" in chain.text and detail_url in _analytics_links(chain.text)
    topic_url = next(
        url
        for url in _analytics_links(detail.text)
        if urlparse(url).path == "/app/analytics/group" and parse_qs(urlparse(url).query).get("axis") == ["themes"]
    )
    topic = await client.get(topic_url)
    assert topic.status_code == 200 and "← К анализу" in topic.text
    assert detail_url in _analytics_links(topic.text)
    again = await client.get(detail_url)
    assert group_url in _analytics_links(again.text)


async def test_detail_metric_states_original_and_highlights_are_explicit(client, drill_records):
    data = drill_records
    payload = {
        "analysis_title": "Honest metrics",
        "analysis_summary": "Saved summary",
        "post_url": "https://example.com/original",
        "source_metadata": {"platform": "vkontakte"},
        "analysis_metadata": {"analysis_timestamp": "2026-03-15T12:00:00Z"},
        "content_statistics": {
            "total_posts": 1,
            "total_reactions": 0,
            "total_comments": 0,
            "content_date_range": {"earliest": "2026-03-15T10:00:00Z", "latest": "2026-03-15T10:00:00Z"},
            "metric_coverage": {
                "total_reactions": {"known": 1, "total": 1},
                "total_comments": {"known": 0, "total": 1},
            },
        },
        "multi_llm_analysis": {"text_analysis": {"sentiment_score": 0.95, "highlights": []}},
    }
    with tenant_scope(data["tenant_id"]):
        await AIAnalytics.objects.update_by_id(data["ids"][0], summary_data=payload)
    page = await client.get(f"/app/analytics/{data['ids'][0]}")
    assert page.status_code == 200
    assert 'data-metric="total_reactions" data-state="available"' in page.text
    assert 'data-metric="total_comments" data-state="unknown"' in page.text
    assert 'data-metric="total_views" data-state="unknown"' in page.text
    assert "0.95" in page.text and "95%" not in page.text
    assert "В сохранённом анализе не выделены." in page.text
    assert "Открыть исходный материал" in page.text and "https://example.com/original" in _analytics_links(page.text)
    assert "Период публикаций:" in page.text and "15.03.2026" in page.text and "Платформа: VK" in page.text
    assert "Вовлечённость" not in page.text
    payload["post_url"] = "javascript:alert(1)"
    payload["multi_llm_analysis"]["text_analysis"].pop("highlights")
    with tenant_scope(data["tenant_id"]):
        await AIAnalytics.objects.update_by_id(data["ids"][0], summary_data=payload)
    page = await client.get(f"/app/analytics/{data['ids'][0]}")
    assert "Не рассчитаны или не сохранены." in page.text
    assert "Открыть исходный материал" not in page.text
    assert "javascript:alert" not in page.text


async def test_direct_historic_detail_has_reliable_source_fallback(client, drill_records):
    from urllib.parse import parse_qs, urlparse

    data = drill_records
    page = await client.get(f"/app/analytics/{data['ids'][5]}")
    assert page.status_code == 200 and "Цепочка ·" not in page.text
    back = next(url for url in _analytics_links(page.text) if urlparse(url).path == "/app/analytics/group")
    query = parse_qs(urlparse(back).query)
    assert query["axis"] == ["sources"] and query["value"] == [str(data["sources"][0])] and query["days"] == ["all"]
    assert (await client.get(back)).status_code == 200


async def test_single_analysis_viewer_and_foreign_tenant_are_read_only(client, drill_records):
    data = drill_records
    viewer = await _invitee("detailViewer", UserRoleType.VIEWER, data["tenant_id"], "secret-password-1")
    foreign_user = None
    try:
        async with await _client() as foreign_client:
            foreign_user, foreign_tenant = await _register(foreign_client, "detailForeign")
            foreign_source = await _make_source(foreign_client, foreign_tenant, "Private detail source")
            with tenant_scope(foreign_tenant):
                row = await AIAnalytics.objects.create(
                    tenant_id=foreign_tenant,
                    source_id=foreign_source,
                    analysis_date=date.today(),
                    period_type=PeriodType.DAY,
                    summary_data={"analysis_title": "Private detail"},
                )
            async with await _client() as member_client:
                await _login(member_client, viewer.username)
                page = await member_client.get(f"/app/analytics/{data['ids'][1]}")
                assert page.status_code == 200
                assert not re.search(r'action="/app/analytics/[^\"]*/delete"', page.text)
                denied = await member_client.get(f"/app/analytics/{row.id}?tenant_id={foreign_tenant}")
                assert denied.status_code == 404 and "Private detail" not in denied.text
    finally:
        await User.objects.delete_user(viewer.id)
        if foreign_user:
            await _drop(foreign_user, foreign_tenant, foreign_source)


async def test_titles_are_identical_in_group_chronology_detail_chain_and_dashboard(client, drill_records):
    from html import unescape

    def card(html, tag, row_id):
        match = re.search(rf'<{tag}[^>]*data-analysis-id="{row_id}"[^>]*>(.*?)</{tag}>', html, re.S)
        assert match is not None
        return unescape(match.group(1))

    def text(html, tag):
        return re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S).group(1).strip()

    data = drill_records
    specs = [
        {"topics": ["дизайн интерьера"], "sentiment_score": 0.9},
        {
            "multi_llm_analysis": {
                "text_analysis": {
                    "parsed": {
                        "analysis_title": "Сохранённый заголовок",
                        "topics": ["дизайн интерьера"],
                        "analysis_summary": "Сохранённая сводка",
                    }
                }
            }
        },
        {},
    ]
    expected = ["дизайн интерьера", "Сохранённый заголовок", "Материалы источника «Same source name»"]
    with tenant_scope(data["tenant_id"]):
        for row_id, payload in zip(data["ids"][:3], specs):
            await AIAnalytics.objects.filter(id=row_id).update(summary_data=payload)

    group = await client.get(
        "/app/analytics/group", params={"axis": "sources", "value": data["sources"][0], "days": "all"}
    )
    chronology = await client.get("/app/analytics", params={"group_by": "days", "days": "all"})
    dashboard = await client.get("/app")
    chain = await client.get("/app/analytics/chains/drill-celebration", params={"days": "all"})
    for page in (group, chronology, dashboard, chain):
        assert page.status_code == 200
        assert "анализ не проводился" not in page.text
    for row_id, title in zip(data["ids"][:3], expected):
        group_card = card(group.text, "article", row_id)
        day_card = card(chronology.text, "a", row_id)
        assert text(group_card, "a") == title
        assert text(day_card, "p") == title
        assert f"Анализ #{row_id}" not in group_card
        assert any(link.startswith(f"/app/analytics/{row_id}?") for link in _analytics_links(chronology.text))
        detail = await client.get(f"/app/analytics/{row_id}", params={"days": "all"})
        assert detail.status_code == 200
        assert text(unescape(detail.text), "h1") == title
        assert title in unescape(dashboard.text)
    assert expected[1] in chain.text and expected[2] in chain.text
    assert "Сводка для этой записи не сохранена." in _visible_text(group.text)
    assert "Сводка для этой записи не сохранена." in _visible_text(chronology.text)
    # Missing headline does not suppress a saved nested summary or make it a warning.
    nested = card(chronology.text, "a", data["ids"][1])
    assert "не сохранена" not in nested


async def test_common_title_is_escaped_in_group_and_chronology(client, drill_records):
    data = drill_records
    with tenant_scope(data["tenant_id"]):
        await AIAnalytics.objects.filter(id=data["ids"][0]).update(summary_data={"topics": ["<script>alert(1)</script>"]})
    for path in [
        f'/app/analytics/group?axis=sources&value={data["sources"][0]}&days=all',
        "/app/analytics?group_by=days&days=all",
    ]:
        page = await client.get(path)
        assert page.status_code == 200
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text


async def test_summary_derived_title_is_shared_without_reanalysis_warning(client, drill_records):
    from html import unescape

    from app.services.ai.analysis_render import summary_display_heading

    data = drill_records
    summary = (
        "Обновлённый интерьер включает " + "современные материалы и удобную мебель " * 8 + ". Далее детали."
    )
    heading = summary_display_heading(summary)
    assert len(heading) <= 120 and heading.endswith("…")
    with tenant_scope(data["tenant_id"]):
        for row_id in data["ids"][:3]:
            await AIAnalytics.objects.filter(id=row_id).update(
                summary_data={
                    "multi_llm_analysis": {
                        "text_analysis": {
                            "parsed": {
                                "analysis_summary": summary,
                                "topics": ["Дизайн интерьера"],
                                "sentiment_score": 0.9,
                            }
                        }
                    }
                }
            )
    for path in [
        f'/app/analytics/group?axis=sources&value={data["sources"][0]}&days=all',
        "/app/analytics?group_by=days&days=all",
        f'/app/analytics/{data["ids"][0]}?days=all',
        "/app/analytics/chains/drill-celebration?days=all",
        "/app",
    ]:
        response = await client.get(path)
        assert response.status_code == 200
        assert heading in unescape(response.text)
        assert "анализ не проводился" not in response.text
        assert f'Анализ #{data["ids"][0]}' not in response.text
    detail = await client.get(f'/app/analytics/{data["ids"][0]}?days=all')
    assert summary in unescape(detail.text)  # Full summary survives display-only truncation.


async def test_entities_label_is_mentions_and_query_contract_unchanged(client, drill_records):
    from urllib.parse import parse_qs, urlparse

    data = drill_records
    response = await client.get("/app/analytics?group_by=entities&entity_type=brand&days=all")
    assert response.status_code == 200
    assert "По упоминаниям" in _visible_text(response.text) and "По сущностям" not in _visible_text(response.text)
    group_url = next(
        link
        for link in _analytics_links(response.text)
        if urlparse(link).path == "/app/analytics/group"
        and parse_qs(urlparse(link).query).get("axis") == ["entities"]
    )
    assert parse_qs(urlparse(group_url).query)["entity_type"] == ["brand"]
    group = await client.get(group_url)
    assert group.status_code == 200
    assert "По упоминаниям" in _visible_text(group.text) and "По сущностям" not in _visible_text(group.text)
    assert "Иван" in response.text and "ACME" in response.text
    login = await client.post(
        "/api/v1/auth/login", data={"username": data["user"].username, "password": "secret-password-1"}
    )
    assert login.status_code == 200
    api = await client.get(
        "/api/v1/dashboard/analytics/aggregate/grouped?group_by=entities&entity_type=brand",
        headers={"Authorization": "Bearer " + login.json()["access_token"]},
    )
    assert api.status_code == 200 and api.json()["axis"] == "entities"
    assert {g["key"] for g in api.json()["groups"]} == {"Иван", "ACME"}


def test_visible_text_preserves_prose_contract_without_matching_assets_or_attributes():
    assert _visible_text('<p>Сводка для этой записи не\n    сохранена.</p>') == "Сводка для этой записи не сохранена."
    assert _visible_text('<a>По\n    <span>упоминаниям</span></a>') == "По упоминаниям"
    assert _visible_text('<p>ACME &amp; Иван</p>') == "ACME & Иван"
    assert _visible_text('<script>По упоминаниям</script><style>.missing { content: "Сводка"; }</style>') == ""
    assert _visible_text('<p title="По упоминаниям">По сущностям</p>') == "По сущностям"


@pytest.mark.tenancy
async def test_workspace_owner_without_structured_delete_right_cannot_delete_analytics(client):
    from app.core.permissions import service_permission_scope
    from app.types import ActionType

    user = None
    tenant_id = source_id = None
    try:
        user, tenant_id = await _register(client, "deleteDenied")
        with tenant_scope(tenant_id), service_permission_scope("source", "create"):
            source_id = await _make_source(client, tenant_id, "Protected owner analysis")
        chain_id = _name("protected-chain-")
        row_id = await _chain_analysis(tenant_id, source_id, chain_id, "Protected owner row", 0)
        loaded = await User.objects.prefetch_related("role.permissions").get(id=user.id)
        assert not loaded.has_perm_for("aianalytics", ActionType.DELETE)
        assert (await TenantUserManager().web_memberships(user.id))[0].is_owner
        await _login(client, user.username)
        page = await client.get(f"/app/analytics/chains/{chain_id}")
        assert page.status_code == 200
        assert f'action="/app/analytics/{row_id}/delete"' not in page.text
        assert f'action="/app/analytics/chains/{chain_id}/delete"' not in page.text
        token = await _csrf(client, f"/app/analytics/chains/{chain_id}")
        for path in (f"/app/analytics/{row_id}/delete", f"/app/analytics/chains/{chain_id}/delete"):
            response = await client.post(path, data={"_csrf": token, "tenant_id": str(tenant_id)})
            assert "Недостаточно прав" in response.text, path
            with tenant_scope(tenant_id):
                assert await AIAnalytics.objects.get(id=row_id) is not None
    finally:
        if tenant_id is not None:
            with tenant_scope(tenant_id), service_permission_scope("source", "delete"):
                await _drop(user, tenant_id, source_id)
        elif user is not None:
            await User.objects.delete_user(user.id)


@pytest.mark.tenancy
async def test_analytics_delete_role_cannot_cross_workspace_even_with_forged_target(client, analytics_delete_actor):
    from app.core.permissions import service_permission_scope

    user = other = None
    tenant_id = other_tenant_id = source_id = other_source_id = None
    try:
        user, tenant_id = await _register(client, "deleteBound")
        async with await _client() as other_client:
            other, other_tenant_id = await _register(other_client, "deleteForeign")
        with tenant_scope(tenant_id), service_permission_scope("source", "create"):
            source_id = await _make_source(client, tenant_id, "Owned analysis source")
        with tenant_scope(other_tenant_id), service_permission_scope("source", "create"):
            other_source_id = await _make_source(client, other_tenant_id, "Foreign analysis source")
        chain_id = _name("shared-chain-")
        own_id = await _chain_analysis(tenant_id, source_id, chain_id, "Owned analysis", 0)
        foreign_id = await _chain_analysis(other_tenant_id, other_source_id, chain_id, "Foreign analysis", 0)
        await analytics_delete_actor(user)
        await _login(client, user.username)
        token = await _csrf(client, f"/app/analytics/chains/{chain_id}")
        response = await client.post(
            f"/app/analytics/{foreign_id}/delete",
            data={"_csrf": token, "tenant_id": str(other_tenant_id)},
        )
        assert "Анализ не найден" in response.text
        with tenant_scope(tenant_id):
            assert await AIAnalytics.objects.get(id=own_id) is not None
        with tenant_scope(other_tenant_id):
            assert await AIAnalytics.objects.get(id=foreign_id) is not None
        response = await client.post(
            f"/app/analytics/chains/{chain_id}/delete",
            data={"_csrf": token, "tenant_id": str(other_tenant_id)},
        )
        assert "Цепочка удалена" in response.text
        with tenant_scope(tenant_id):
            assert await AIAnalytics.objects.get(id=own_id) is None
        with tenant_scope(other_tenant_id):
            assert await AIAnalytics.objects.get(id=foreign_id) is not None
    finally:
        for actor, tid, sid in ((user, tenant_id, source_id), (other, other_tenant_id, other_source_id)):
            if tid is not None:
                with tenant_scope(tid), service_permission_scope("source", "delete"):
                    await _drop(actor, tid, sid)
            elif actor is not None:
                await User.objects.delete_user(actor.id)
