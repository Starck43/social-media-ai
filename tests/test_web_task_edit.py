"""Task form parity with the payload model.

Both the editor page and the add modal must expose the payload keys the
runtime actually reads: the analysis targets (brands, competitors, …)
conditional on the scenario's analysis types, digest parameters for digest
tasks, one force flag per job type, and values that round-trip back into the
form. Real PostgreSQL, same policy as the rest of the suite.
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentScenario, AgentTask, User
from app.models.managers.tenant_manager import TenantUserManager, tenants

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


async def _make_scenario(tenant_id: int, analysis_types: list[str]) -> AgentScenario:
    with tenant_scope(bypass=True):
        return await AgentScenario.objects.create(
            name=_name("scenario"),
            analysis_types=analysis_types,
            is_active=True,
            tenant_id=tenant_id,
        )


async def _make_task(
    tenant_id: int,
    job_type: str = "analyze",
    payload: dict | None = None,
    scenario_id: int | None = None,
) -> AgentTask:
    with tenant_scope(bypass=True):
        return await AgentTask.objects.create(
            name=_name("task"),
            job_type=job_type,
            cron_expr="0 * * * *",
            payload=payload if payload is not None else {"cli_dates": {"start_date": "2026-09-01"}},
            agent_scenario_id=scenario_id,
            is_active=False,
            tenant_id=tenant_id,
        )


async def _delete(task: AgentTask, scenario: AgentScenario, user: User, tenant_id: int) -> None:
    with tenant_scope(bypass=True):
        await AgentTask.objects.delete(id=task.id)
        await AgentScenario.objects.delete(id=scenario.id)
    await User.objects.delete_user(user.id)
    await tenants.delete_by_id(tenant_id)


@pytest.fixture
async def client():
    async with await _client() as c:
        yield c


async def test_edit_page_renders_targets_for_the_bound_scenario(client: AsyncClient) -> None:
    """The scenario's analysis types pick which target inputs the form shows.

    `brand_mentions` wants `brands`, `keywords` wants `keywords_list` — the
    mapping the analyzer reads from payload has to be editable here, and the
    scenario's own id reaches the Alpine state to drive it. The force flags
    stay one checkbox per type: the second `force_reanalyze` control that used
    to sit in the monitored/excluded grid posted the same field name.
    """
    user, tenant_id = await _register(client, "EditTargets")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id, ["brand_mentions", "keywords"])
        task = await _make_task(
            tenant_id,
            scenario_id=scenario.id,
            payload={"cli_dates": {"start_date": "2026-09-01"}, "brands": ["Coca Cola"]},
        )
        page = await client.get(f"/app/tasks/{task.id}/edit")
        assert page.status_code == 200

        assert f'"{scenario.id}": ["brand_mentions", "keywords"]' in page.text
        # The inputs are rendered from TARGET_FIELDS, one entry per payload key.
        assert 'key: "brands"' in page.text
        assert 'key: "keywords_list"' in page.text
        assert ':name="f.key"' in page.text
        assert "Бренды" in page.text
        # Values round-trip: the stored brand is bound to its input.
        assert "Coca Cola" in page.text
        # The user list inputs survive for the types that read them.
        assert 'name="monitored_users"' in page.text
        assert 'name="excluded_users"' in page.text
        # One force flag per job type, not two controls on one field name.
        assert page.text.count('name="force_reanalyze"') == 1
        assert "Переанализировать всё (игнорировать хеши)" not in page.text
        assert "Принудительный повторный анализ" in page.text
        assert 'name="force_refresh"' in page.text
    finally:
        if task is not None and scenario is not None:
            await _delete(task, scenario, user, tenant_id)


async def test_update_round_trips_target_values(client: AsyncClient) -> None:
    """A save stores the targets as lists, an empty field drops its key."""
    user, tenant_id = await _register(client, "EditStore")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id, ["brand_mentions", "keywords"])
        task = await _make_task(tenant_id, scenario_id=scenario.id)
        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")
        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "analyze",
                "cron_expr": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "brands": "Coca Cola, Sprite",
                "keywords_list": "жалоба",
                "competitors": "",
                "hashtags": "",
                "influencer_names": "",
                "topic_list": "",
                "_csrf": csrf,
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        # Commas only: a target may contain a space that a username split
        # would have broken.
        assert updated.payload["brands"] == ["Coca Cola", "Sprite"]
        assert updated.payload["keywords_list"] == ["жалоба"]
        assert "competitors" not in updated.payload
        assert updated.payload["cli_dates"] == {"start_date": "2026-09-01"}
    finally:
        if task is not None and scenario is not None:
            await _delete(task, scenario, user, tenant_id)


async def test_update_clears_emptied_target_and_warns_about_missing_ones(client: AsyncClient) -> None:
    """Clearing a field removes its payload key, and the save warns which
    targets the bound scenario still expects."""
    user, tenant_id = await _register(client, "EditClear")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id, ["brand_mentions"])
        task = await _make_task(
            tenant_id,
            scenario_id=scenario.id,
            payload={"cli_dates": {"start_date": "2026-09-01"}, "brands": ["Old brand"]},
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
                "brands": "",
                "_csrf": csrf,
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        assert "brands" not in updated.payload
        # The warning follows the scenario this save binds.
        assert "укажите в параметрах задачи" in resp.text
        assert "бренды" in resp.text
    finally:
        if task is not None and scenario is not None:
            await _delete(task, scenario, user, tenant_id)


async def test_collect_force_refresh_is_the_full_cycle_flag(client: AsyncClient) -> None:
    """The single collect checkbox sets both flags (docs/CLI.md full cycle),
    and unchecking it clears the stored ones instead of leaving a stale value."""
    user, tenant_id = await _register(client, "EditForce")
    scenario = task = None
    try:
        scenario = await _make_scenario(tenant_id, ["themes"])
        task = await _make_task(
            tenant_id,
            job_type="collect",
            scenario_id=scenario.id,
            payload={"cli_dates": {"start_date": "2026-09-01"}, "force_reanalyze": True},
        )
        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")

        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "collect",
                "cron_expr": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "force_refresh": "on",
                "_csrf": csrf,
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        assert updated.payload["force_refresh"] is True
        assert updated.payload["force_reanalyze"] is True

        csrf = await _csrf(client, f"/app/tasks/{task.id}/edit")
        resp = await client.post(
            f"/app/tasks/{task.id}",
            data={
                "name": task.name,
                "job_type": "collect",
                "cron_expr": "0 * * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "_csrf": csrf,
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            updated = await AgentTask.objects.get(id=task.id)
        assert updated.payload["force_refresh"] is False
        assert updated.payload["force_reanalyze"] is False
    finally:
        if task is not None and scenario is not None:
            await _delete(task, scenario, user, tenant_id)


async def test_tasks_page_add_modal_offers_targets_and_digest_params(client: AsyncClient) -> None:
    """The add modal carries the same conditional blocks as the editor.

    The scenario select is bound to the Alpine state so its analysis types
    pick the target inputs, digest tasks get period/grouping-axis instead of
    trigger fields, and the user lists are `x-if` gated so they do not reach
    the POST for the types that do not read them.
    """
    user, tenant_id = await _register(client, "AddModal")
    scenario = None
    try:
        scenario = await _make_scenario(tenant_id, ["brand_mentions"])
        page = await client.get("/app/tasks")
        assert page.status_code == 200

        assert 'x-data="addTaskForm()"' in page.text
        assert 'x-model="task.scenario_id"' in page.text
        assert f'"{scenario.id}": ["brand_mentions"]' in page.text
        assert 'key: "brands"' in page.text
        assert 'key: "topic_list"' in page.text
        # The target inputs follow the selected scenario's analysis types.
        assert 'x-show="targetFields.length > 0"' in page.text
        # Digest params exist in the modal — group_by was edit-page-only.
        assert 'name="digest_period"' in page.text
        assert 'name="digest_group_by"' in page.text
        assert "task.job_type === 'digest'" in page.text
        # User lists post only for the types that read them.
        assert 'x-if="task.job_type === \'collect\'"' in page.text
        # One force flag per job type, the same macro the editor uses.
        assert page.text.count('name="force_reanalyze"') == 1
        assert "Переанализировать всё (игнорировать хеши)" not in page.text
    finally:
        if scenario is not None:
            with tenant_scope(bypass=True):
                await AgentScenario.objects.delete(id=scenario.id)
        await User.objects.delete_user(user.id)
        await tenants.delete_by_id(tenant_id)


async def test_create_stores_targets_for_the_bound_scenario(client: AsyncClient) -> None:
    """The add modal posts its target fields into AgentTask.payload.

    Filled fields become lists split on commas only — a target may contain
    a space — and a scenario that expects brands gets them instead of a
    "brands не переданы" warning on the landing page.
    """
    user, tenant_id = await _register(client, "AddStore")
    scenario = created = None
    try:
        scenario = await _make_scenario(tenant_id, ["brand_mentions", "keywords"])
        csrf = await _csrf(client, "/app/tasks")
        name = _name("task")
        resp = await client.post(
            "/app/tasks",
            data={
                "name": name,
                "job_type": "collect",
                "cron_custom": "0 9 * * *",
                "scenario_id": str(scenario.id),
                "start_date": "2026-09-01",
                "brands": "Coca Cola, Sprite",
                "keywords_list": "жалоба",
                "_csrf": csrf,
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            created = await AgentTask.objects.filter(name=name).first()
        assert created is not None
        assert created.agent_scenario_id == scenario.id
        assert created.payload["brands"] == ["Coca Cola", "Sprite"]
        assert created.payload["keywords_list"] == ["жалоба"]
        assert "укажите в параметрах задачи" not in resp.text
    finally:
        if created is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete(id=created.id)
        if scenario is not None:
            with tenant_scope(bypass=True):
                await AgentScenario.objects.delete(id=scenario.id)
        await User.objects.delete_user(user.id)
        await tenants.delete_by_id(tenant_id)


async def test_create_digest_task_stores_grouping_params(client: AsyncClient) -> None:
    """A digest task created from the modal carries period and grouping axis."""
    user, tenant_id = await _register(client, "AddDigest")
    scenario = created = None
    try:
        scenario = await _make_scenario(tenant_id, ["themes"])
        csrf = await _csrf(client, "/app/tasks")
        name = _name("task")
        resp = await client.post(
            "/app/tasks",
            data={
                "name": name,
                "job_type": "digest",
                "cron_custom": "0 9 * * *",
                "scenario_id": str(scenario.id),
                "digest_period": "week",
                "digest_group_by": "sources",
                "digest_time_breakdown": "on",
                "_csrf": csrf,
            },
        )
        assert resp.status_code == 200
        with tenant_scope(bypass=True):
            created = await AgentTask.objects.filter(name=name).first()
        assert created is not None
        assert created.payload["period"] == "week"
        assert created.payload["group_by"] == "sources"
        assert created.payload["time_breakdown"] is True
    finally:
        if created is not None:
            with tenant_scope(bypass=True):
                await AgentTask.objects.delete(id=created.id)
        if scenario is not None:
            with tenant_scope(bypass=True):
                await AgentScenario.objects.delete(id=scenario.id)
        await User.objects.delete_user(user.id)
        await tenants.delete_by_id(tenant_id)
