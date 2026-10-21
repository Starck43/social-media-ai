"""Task editor: task-owned collection intent and the source picker.

The editor is the first writable surface for `payload["collection"]` (stage E
of the collection plan). These tests lock the contract the runtime parser
reads: the form writes the normalised block, the page round-trips it, a
malformed value fails the save instead of collecting a different population,
and a save without JavaScript keeps targets, dates and the collection block.
They also lock the picker: sources are grouped by type and a task-scoped
query source (`owner_task_id`) is never offered to another task.

Real PostgreSQL, same policy as the rest of the suite.
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentScenario, AgentTask, Platform, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import SourceType

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


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


async def _make_scenario(tenant_id: int, analysis_types: list[str] | None = None) -> AgentScenario:
    with tenant_scope(bypass=True):
        return await AgentScenario.objects.create(
            name=_name("scenario"),
            analysis_types=analysis_types or ["keywords"],
            is_active=True,
            tenant_id=tenant_id,
        )


async def _make_task(
    tenant_id: int,
    payload: dict | None = None,
    scenario_id: int | None = None,
) -> AgentTask:
    with tenant_scope(bypass=True):
        return await AgentTask.objects.create(
            name=_name("task"),
            job_type="analyze",
            cron_expr="0 * * * *",
            payload=payload if payload is not None else {"cli_dates": {"start_date": "2026-09-01"}},
            agent_scenario_id=scenario_id,
            is_active=False,
            tenant_id=tenant_id,
        )


async def _make_source(
    tenant_id: int,
    name: str,
    source_type: SourceType = SourceType.GROUP,
    owner_task_id: int | None = None,
) -> Source:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(bypass=True):
        return await Source.objects.create(
            name=name,
            platform_id=platform.id,
            external_id=f"{name}-{secrets.token_hex(4)}",
            source_type=source_type,
            is_active=True,
            params={},
            tenant_id=tenant_id,
            owner_task_id=owner_task_id,
        )


async def _cleanup(user: User, tenant_id: int, *rows) -> None:
    with tenant_scope(bypass=True):
        for model, row in rows:
            await model.objects.delete(id=row.id)
    await User.objects.delete_user(user.id)
    await tenants.delete_by_id(tenant_id)


@pytest.fixture
async def client():
    async with await _client() as c:
        yield c


def _collection_form(**overrides) -> dict:
    data = {
        "collection_configured": "1",
        "collection_city": "Киров",
        "collection_city_mode": "prefer",
        "collection_people": "on",
        "collection_communities": "on",
        "collection_discovery_queries": "дизайн интерьера, ремонт",
        "collection_queries": "Древиз, Италия поездка",
        "collection_content_types": ["posts", "comments"],
        "collection_engagement_types": ["likes"],
        "collection_limit": "500",
        "collection_comments_max_per_post": "50",
        "collection_comments_total": "1500",
        "collection_candidates_limit": "20",
        "collection_candidates_confirm": "auto",
    }
    data.update(overrides)
    return data


EXPECTED_COLLECTION = {
    "version": 1,
    "content_types": ["posts", "comments"],
    "engagement_types": ["likes"],
    "limit": 500,
    "queries": ["Древиз", "Италия поездка"],
    "discovery": {
        "people": True,
        "communities": True,
        "queries": ["дизайн интерьера", "ремонт"],
    },
    "comments": {"max_per_post": 50, "total": 1500},
    "candidates": {"limit": 20, "confirm": "auto"},
    "city": {"name": "Киров", "mode": "prefer"},
    "date_window": {"start": "2026-09-01", "end": None},
}


async def test_create_stores_the_canonical_collection_block(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "CollectCreate")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id)
        name = _name("collect")
        csrf = await _csrf(client, "/app/tasks")
        resp = await client.post(
            "/app/tasks",
            data={
                "name": name,
                "job_type": "analyze",
                "cron_custom": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "_csrf": csrf,
                **_collection_form(),
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.get(name=name)
        assert task is not None
        assert task.payload["collection"] == EXPECTED_COLLECTION
        # The legacy date keys stay in sync: other readers still use them.
        assert task.payload["cli_dates"] == {"start_date": "2026-09-01"}
    finally:
        if task is not None and scenario is not None:
            await _cleanup(user, tenant_id, (AgentTask, task), (AgentScenario, scenario))
        elif scenario is not None:
            await _cleanup(user, tenant_id, (AgentScenario, scenario))


async def test_edit_page_round_trips_the_collection_block(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "CollectRead")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id)
        task = await _make_task(
            tenant_id,
            payload={
                "cli_dates": {"start_date": "2026-09-01"},
                "collection": EXPECTED_COLLECTION,
            },
            scenario_id=scenario.id,
        )
        page = await client.get(f"/app/tasks/{task.id}/edit")
        assert page.status_code == 200
        assert 'name="collection_configured" value="1"' in page.text
        assert 'name="collection_city"' in page.text
        assert 'value="Киров"' in page.text
        assert 'value="Древиз, Италия поездка"' in page.text
        assert 'value="500"' in page.text
        assert 'value="50"' in page.text
        assert 'value="1500"' in page.text
        assert 'value="20"' in page.text
        # Checked boxes are server-rendered, not Alpine-only.
        assert re.search(r'name="collection_content_types" value="posts"[^>]*checked', page.text)
        assert re.search(r'name="collection_content_types" value="comments"[^>]*checked', page.text)
        assert re.search(r'name="collection_engagement_types" value="likes"[^>]*checked', page.text)
        assert not re.search(r'name="collection_engagement_types" value="reposts"[^>]*checked', page.text)
        # The section headings the split editor promises.
        assert "Кого искать" in page.text
        assert "Что искать" in page.text
        assert "Где читать" in page.text
        assert "Лимиты комментариев" in page.text
    finally:
        if task is not None and scenario is not None:
            await _cleanup(user, tenant_id, (AgentTask, task), (AgentScenario, scenario))


async def test_update_rewrites_and_clears_the_block(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "CollectWrite")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id)
        task = await _make_task(
            tenant_id,
            payload={
                "cli_dates": {"start_date": "2026-09-01"},
                "collection": EXPECTED_COLLECTION,
            },
            scenario_id=scenario.id,
        )
        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")
        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "analyze",
                "cron_expr": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "_csrf": csrf,
                # A city cleared in the form must disappear from the block.
                **_collection_form(
                    collection_city="",
                    collection_city_mode="prefer",
                    collection_queries="",
                    collection_people="",
                    collection_communities="",
                    collection_discovery_queries="",
                    collection_content_types=["comments"],
                    collection_engagement_types=[],
                ),
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        block = updated.payload["collection"]
        assert block["content_types"] == ["comments"]
        assert block["engagement_types"] == []
        assert block["queries"] == []
        assert block["discovery"] == {"people": False, "communities": False, "queries": []}
        assert "city" not in block

        # Switching away from analysis drops the block entirely.
        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")
        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "digest",
                "cron_expr": "0 * * * *",
                "digest_period": "day",
                "_csrf": csrf,
                **_collection_form(),
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        assert "collection" not in updated.payload
    finally:
        if task is not None and scenario is not None:
            await _cleanup(user, tenant_id, (AgentTask, task), (AgentScenario, scenario))


async def test_malformed_collection_fails_the_save(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "CollectBad")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id)
        stored = {
            "cli_dates": {"start_date": "2026-09-01"},
            "collection": EXPECTED_COLLECTION,
        }
        task = await _make_task(tenant_id, payload=stored, scenario_id=scenario.id)
        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")
        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "analyze",
                "cron_expr": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "_csrf": csrf,
                **_collection_form(collection_limit="999999"),
            },
        )
        assert resp.status_code == 200
        assert "Проверьте параметры сбора" in resp.text
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        assert updated.payload["collection"] == EXPECTED_COLLECTION

        # An unsupported content type is rejected the same way.
        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")
        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "analyze",
                "cron_expr": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "_csrf": csrf,
                **_collection_form(collection_content_types=["stories"]),
            },
        )
        assert resp.status_code == 200
        assert "Проверьте параметры сбора" in resp.text
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        assert updated.payload["collection"]["content_types"] == ["posts", "comments"]
    finally:
        if task is not None and scenario is not None:
            await _cleanup(user, tenant_id, (AgentTask, task), (AgentScenario, scenario))


async def test_no_js_save_keeps_targets_dates_and_collection(client: AsyncClient) -> None:
    """The page renders every value a plain POST needs.

    Without Alpine the edit page cannot build x-for/x-if fields; the browser
    submits only what the server rendered. Those inputs must round-trip the
    task instead of clearing its targets and dates.
    """
    user, tenant_id = await _register(client, "CollectNoJs")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id, ["brand_mentions", "keywords"])
        task = await _make_task(
            tenant_id,
            payload={
                "cli_dates": {"start_date": "2026-09-01", "end_date": "2026-09-30"},
                "brands": ["Coca Cola"],
                "keywords_list": ["жалоба"],
                "collection": EXPECTED_COLLECTION,
            },
            scenario_id=scenario.id,
        )
        page = await client.get(f"/app/tasks/{task.id}/edit")
        assert page.status_code == 200
        # Server-rendered values are what a no-JS submission carries.
        assert 'name="brands" value="Coca Cola"' in page.text
        assert 'name="keywords_list" value="жалоба"' in page.text
        assert 'name="start_date"' in page.text
        assert 'value="2026-09-01"' in page.text
        assert "Редактор требует JavaScript" not in page.text
        assert 'disabled class="m-0 min-w-0' not in page.text

        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")
        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "analyze",
                "cron_expr": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
                "brands": "Coca Cola",
                "keywords_list": "жалоба",
                "competitors": "",
                "hashtags": "",
                "influencer_names": "",
                "topic_list": "",
                "tracked_users": "",
                "keywords_match": "any",
                "trigger_type": "",
                "action_type": "",
                "_csrf": csrf,
                **_collection_form(),
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        assert updated.payload["brands"] == ["Coca Cola"]
        assert updated.payload["keywords_list"] == ["жалоба"]
        assert updated.payload["cli_dates"] == {"start_date": "2026-09-01", "end_date": "2026-09-30"}
        assert updated.payload["collection"] == {
            **EXPECTED_COLLECTION,
            "date_window": {"start": "2026-09-01", "end": "2026-09-30"},
        }
    finally:
        if task is not None and scenario is not None:
            await _cleanup(user, tenant_id, (AgentTask, task), (AgentScenario, scenario))


async def test_source_picker_groups_by_type_and_hides_foreign_task_sources(client: AsyncClient) -> None:
    user, tenant_id = await _register(client, "CollectSources")
    scenario = task = other_task = None
    group_source = own_search = foreign_search = None
    try:
        scenario = await _make_scenario(tenant_id)
        task = await _make_task(tenant_id, scenario_id=scenario.id)
        other_task = await _make_task(tenant_id, scenario_id=scenario.id)
        group_source = await _make_source(tenant_id, "Group source", SourceType.GROUP)
        own_search = await _make_source(tenant_id, "Own query", SourceType.SEARCH, owner_task_id=task.id)
        foreign_search = await _make_source(tenant_id, "Other query", SourceType.SEARCH, owner_task_id=other_task.id)

        page = await client.get(f"/app/tasks/{task.id}/edit")
        assert page.status_code == 200
        # Grouped by source type: the type label is a heading in the fallback.
        assert "Группа" in page.text
        assert "Group source" in page.text
        # The task's own query lane stays visible (it can be unlinked), but is
        # marked scoped so the add picker never offers it again.
        assert "Own query" in page.text
        assert '"scoped": true' in page.text
        # Another task's query lane is not part of this page at all.
        assert "Other query" not in page.text

        create_page = await client.get("/app/tasks")
        assert create_page.status_code == 200
        assert "Group source" in create_page.text
        assert "Own query" not in create_page.text
        assert "Other query" not in create_page.text
    finally:
        rows = [(Source, row) for row in (foreign_search, own_search, group_source) if row is not None]
        if task is not None:
            rows.append((AgentTask, task))
        if other_task is not None:
            rows.append((AgentTask, other_task))
        if scenario is not None:
            rows.append((AgentScenario, scenario))
        await _cleanup(user, tenant_id, *rows)
