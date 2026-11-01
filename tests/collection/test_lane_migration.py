"""Task-scoped collection lanes: the plan is pure, the apply is the only writer.

The migration moves an existing task onto search lanes that belong to it: one
`Source` row per phrase, linked to the task and readable by nobody else. The
contract these tests pin:

1. planning is pure and refuses a task that never collects, an empty query
   list, an unresolvable platform and a retirement that would touch another
   task's lane;
2. the collection block is stored through the shared writer, so the migrated
   task carries exactly the canonical shape;
3. applying twice creates no duplicate lane and keeps the same links;
4. a retired legacy source is deactivated and unlinked, never deleted;
5. the apply refuses to run against a schema without `sources.owner_task_id`.

The first group runs without a database; the second one runs against the real
database and is marked `@pytest.mark.tenancy` (it builds its own workspace and
asserts the tenant guard holds).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import ProgrammingError

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Platform, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.source_manager import SourceManager
from app.models.managers.tenant_manager import tenants
from app.services.collection.lane_migration import (
    LaneMigrationError,
    apply_lane_migration,
    plan_lane_migration,
)
from app.types import SourceType
from tests.shared.platform_catalog import shared_platform


def _task(**overrides) -> SimpleNamespace:
    """A task row as the planner sees it: identity, job type, payload, sources."""
    data = {
        "id": 7,
        "name": "Упоминания",
        "tenant_id": 3,
        "job_type": "collect",
        "payload": {},
        "sources": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _source(
    source_id: int,
    *,
    platform: str = "vk",
    source_type: str = "search",
    external_id: str = "legacy",
    owner_task_id: int | None = None,
) -> SimpleNamespace:
    """A linked source row as the planner sees it."""
    return SimpleNamespace(
        id=source_id,
        platform=SimpleNamespace(platform_type=platform),
        source_type=source_type,
        external_id=external_id,
        owner_task_id=owner_task_id,
    )


def _plan(task, **overrides):
    """Plan a task with the CLI defaults."""
    options = {
        "queries": ["доставка"],
        "content_types": ["posts"],
        "limit": 1000,
        "city": None,
        "city_mode": "prefer",
        "date_from": None,
        "date_to": None,
        "retire_source_ids": [],
        "platform": "vk",
    }
    options.update(overrides)
    return plan_lane_migration(task, **options)


# --- planning (no database) ------------------------------------------------


def test_plan_builds_one_lane_per_query() -> None:
    plan = _plan(_task(), queries=["доставка", " Доставка ", "пицца"])

    assert [spec.query for spec in plan.lane_specs] == ["доставка", "пицца"]
    spec = plan.lane_specs[0]
    assert spec.platform == "vk"
    assert spec.name == "Поиск: доставка"
    assert spec.params == {"query": "доставка", "mode": "auto"}
    assert spec.owner_task_id == 7
    assert spec.source_type == "SEARCH"
    digest = hashlib.sha1("доставка".casefold().encode("utf-8")).hexdigest()[:10]
    assert spec.external_id == f"task-7-vk-query-{digest}"


def test_plan_stores_the_canonical_collection_block() -> None:
    plan = _plan(
        _task(),
        queries=["доставка", "пицца"],
        content_types=["posts", "comments"],
        limit=250,
        city="Киров",
        city_mode="prefer",
        date_from="2026-10-01",
        date_to="2026-10-20",
    )

    block = plan.collection_block
    assert block["version"] == 1
    assert block["content_types"] == ["posts", "comments"]
    assert block["limit"] == 250
    assert block["queries"] == ["доставка", "пицца"]
    assert block["city"] == {"name": "Киров", "mode": "prefer"}
    assert block["date_window"] == {"start": "2026-10-01", "end": "2026-10-20"}


def test_plan_keeps_the_settings_it_does_not_manage() -> None:
    task = _task(payload={"period": "week", "collection": {"comments": {"max_per_post": 5, "total": 40}}})

    plan = _plan(task)

    assert plan.collection_block["comments"] == {"max_per_post": 5, "total": 40}


def test_plan_refuses_to_retire_another_tasks_lane() -> None:
    task = _task(sources=[_source(11, owner_task_id=99)])

    with pytest.raises(LaneMigrationError, match="another task"):
        _plan(task, retire_source_ids=[11])


def test_plan_never_links_another_tasks_lane() -> None:
    task = _task(sources=[_source(11, owner_task_id=99), _source(12)])

    plan = _plan(task)

    assert plan.keep_source_ids == [12]


def test_plan_refuses_a_task_that_never_collects() -> None:
    with pytest.raises(LaneMigrationError, match="never collects"):
        _plan(_task(job_type="digest"))


def test_plan_refuses_an_empty_query_list() -> None:
    with pytest.raises(LaneMigrationError, match="at least one search phrase"):
        _plan(_task(), queries=[" ", ""])


def test_plan_infers_the_platform_from_the_linked_sources() -> None:
    plan = _plan(_task(sources=[_source(12, platform="vk")]), platform=None)

    assert plan.platform == "vk"
    assert any("inferred" in warning for warning in plan.warnings)


def test_plan_refuses_sources_that_disagree_on_the_platform() -> None:
    task = _task(sources=[_source(12, platform="vk"), _source(13, platform="telegram")])

    with pytest.raises(LaneMigrationError, match="several platforms"):
        _plan(task, platform=None)


def test_plan_refuses_an_unresolvable_platform() -> None:
    with pytest.raises(LaneMigrationError, match="--platform"):
        _plan(_task(), platform=None)


# --- applying (real database) ----------------------------------------------


@pytest.fixture
async def workspace():
    """A fresh workspace so the tenant guard is exercised for real."""
    tenant = await tenants.create(name="Lane migration", slug=f"lane{secrets.token_hex(4)}")
    yield tenant
    await tenants.delete_by_id(tenant.id)


@pytest.fixture
async def platform():
    """The VK platform row the lanes point at."""
    existing = await Platform.objects.filter(platform_type="vk").first()
    if existing is not None:
        yield existing
        return
    created = await shared_platform("vk")
    yield created


@pytest.fixture
async def task(workspace):
    """A collecting task in the workspace, without any linked source."""
    with tenant_scope(workspace.id), operator_permission_scope():
        created = await AgentTask.objects.create(
            name=f"lane-{secrets.token_hex(3)}",
            cron_expr="0 9 * * *",
            job_type="collect",
            payload={},
            is_active=True,
            next_run_at=None,
        )
    yield created
    with tenant_scope(workspace.id), operator_permission_scope():
        await AgentTask.objects.delete_by_id(created.id)


async def _load(task_id: int):
    """The task with its linked sources — the planner reads both."""
    return await AgentTask.objects.filter(id=task_id).prefetch_related("sources", "sources.platform").first()


@pytest.mark.tenancy
async def test_apply_creates_the_lanes_once(workspace, task, platform) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        plan = _plan(await _load(task.id))

        first = await apply_lane_migration(plan)
        second = await apply_lane_migration(plan)

        assert first.created_source_ids
        assert second.created_source_ids == [], "a second apply creates no duplicate lane"
        assert second.existing_source_ids == first.created_source_ids
        assert second.linked_source_ids == first.linked_source_ids
        lanes = await Source.objects.filter(owner_task_id=task.id)
        assert [row.external_id for row in lanes] == [plan.lane_specs[0].external_id]


@pytest.mark.tenancy
async def test_apply_writes_the_canonical_collection_block(workspace, task, platform) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        plan = _plan(
            await _load(task.id),
            limit=250,
            city="Киров",
            city_mode="require",
            date_from="2026-10-01",
            date_to="2026-10-20",
        )

        await apply_lane_migration(plan)

        refreshed = await AgentTask.objects.get(id=task.id)
        block = refreshed.payload["collection"]
        assert block["queries"] == ["доставка"]
        assert block["limit"] == 250
        assert block["city"] == {"name": "Киров", "mode": "require"}
        assert block["date_window"] == {"start": "2026-10-01", "end": "2026-10-20"}


@pytest.mark.tenancy
async def test_a_retired_legacy_source_is_deactivated_and_unlinked(workspace, task, platform) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        legacy = await Source.objects.create(
            platform_id=platform.id,
            name="Поиск: доставка",
            source_type=SourceType.SEARCH,
            external_id=f"legacy-{secrets.token_hex(3)}",
            params={"query": "доставка"},
            is_active=True,
        )
        await AgentTaskManager().set_sources(task.id, [legacy.id])

        plan = _plan(await _load(task.id), retire_source_ids=[legacy.id])
        result = await apply_lane_migration(plan)

        assert result.retired_source_ids == [legacy.id]
        row = await Source.objects.get(id=legacy.id)
        assert row is not None, "a retired source keeps its row and its trail"
        assert row.is_active is False
        assert legacy.id not in await AgentTaskManager().get_sources(task.id)


@pytest.mark.tenancy
async def test_apply_refuses_a_lane_owned_by_another_task(workspace, task, platform) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        other = await AgentTask.objects.create(
            name=f"other-{secrets.token_hex(3)}",
            cron_expr="0 9 * * *",
            job_type="collect",
            payload={},
            is_active=True,
            next_run_at=None,
        )
        plan = _plan(await _load(task.id))
        await Source.objects.create(
            platform_id=platform.id,
            name=plan.lane_specs[0].name,
            source_type=SourceType.SEARCH,
            external_id=plan.lane_specs[0].external_id,
            params={},
            owner_task_id=other.id,
            is_active=True,
        )

        with pytest.raises(LaneMigrationError, match="another task"):
            await apply_lane_migration(plan)


@pytest.mark.tenancy
async def test_apply_refuses_a_schema_without_the_owner_column(workspace, task, platform, monkeypatch) -> None:
    def _missing_column(*args, **kwargs):
        raise ProgrammingError(
            "SELECT sources.id FROM sources",
            {},
            Exception('column "owner_task_id" does not exist'),
        )

    with tenant_scope(workspace.id), operator_permission_scope():
        plan = _plan(await _load(task.id))
        monkeypatch.setattr(SourceManager, "filter", _missing_column)

        with pytest.raises(LaneMigrationError, match="alembic upgrade head"):
            await apply_lane_migration(plan)

        refreshed = await AgentTask.objects.get(id=task.id)
        assert "collection" not in refreshed.payload, "a refused apply writes nothing"
        assert await AgentTaskManager().get_sources(task.id) == []
