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
        assert 'name="sentiment"' in response.text and 'name="media"' in response.text
        assert 'href="?group_by=sentiment' not in response.text
        assert 'href="?group_by=content_type' not in response.text
        # Filter state survives switching axis/entity type/period.
        from html import unescape
        links = [unescape(href) for href in re.findall(r'href="([^"]*group_by=[^"]*)"', response.text)]
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
