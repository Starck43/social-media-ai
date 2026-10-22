"""The runtime's own mention lanes: the task's phrases become its lanes.

Before the sources are resolved, every phrase of ``payload["collection"]``
becomes a task-owned SEARCH lane through the shared ``ensure_query_lanes``
rules, and every collected source writes one ``collection_lane_runs`` evidence
row beside its per-source counters. The contract these tests pin:

1. phrase derivation is pure: ``any`` keeps one lane per phrase, ``all``
   collapses the phrases into ONE joined lane, and the operator note stays
   one bounded sentence;
2. a phrase without a lane gets a task-owned lane — the planner's derived
   external id, ``params={"query": ..., "mode": "auto"}``, linked to the task —
   and a second run creates no duplicate and changes no link;
3. a lane owned by another task is refused: never taken, never linked;
4. a dry run creates nothing and reports the phrases without a lane;
5. a collected wall/search source writes one evidence row with the
   per-source counts, the task's limit and ``limit_reached``; a failed source
   writes the bounded error code, never raw provider text.

The first group runs without a database; the rest run against the real
database and are marked ``@pytest.mark.tenancy``. The collector is doubled in
every run test; no test touches the network.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, CollectionLaneRun, Platform, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import tenants
from app.services.collection.intent import parse_collection_intent
from app.services.collection.mention_lanes import mention_lane_queries, mention_note
from app.services.collection.planner import query_source_spec
from app.types import SourceType

# --- phrase derivation (no database) ----------------------------------------


def _intent(**collection: Any):
    """An intent over two phrases, with the given collection overrides."""
    block: dict[str, Any] = {"queries": ["доставка", "пицца"]}
    block.update(collection)
    return parse_collection_intent({"collection": block})


def test_any_mode_keeps_one_lane_per_phrase() -> None:
    assert mention_lane_queries(_intent(queries_match="any")) == ("доставка", "пицца")
    assert mention_lane_queries(_intent()) == ("доставка", "пицца"), "any is the default mode"


def test_all_mode_collapses_the_phrases_into_one_joined_lane() -> None:
    assert mention_lane_queries(_intent(queries_match="all")) == ("доставка пицца",)


def test_no_phrases_mean_no_lanes() -> None:
    assert mention_lane_queries(parse_collection_intent({})) == ()


def test_the_dry_run_note_reports_the_phrases_without_a_lane() -> None:
    note = mention_note({"dry_run": True, "total": 3, "missing": ["доставка", "пицца"]})

    assert "без веток 2 из 3" in note
    assert "сухой прогон" in note
    assert len(note) <= 200


def test_the_error_note_stays_one_bounded_sentence() -> None:
    long_note = mention_note({"error_code": "lane_migration_error", "error_hint": "причина " * 60})
    assert len(long_note) <= 200, "an over-long hint never grows the note past the bound"

    note = mention_note({"error_code": "lane_migration_error", "error_hint": "платформа не настроена"})
    assert "lane_migration_error" in note
    assert len(note) <= 200


# --- the lane lifecycle (real database) ---------------------------------------


@pytest.fixture
async def workspace():
    """A fresh workspace so the tenant guard is exercised for real."""
    tenant = await tenants.create(name="Mention lanes", slug=f"mention{secrets.token_hex(4)}")
    yield tenant
    await tenants.delete_by_id(tenant.id)


@pytest.fixture
async def platform():
    """The VK platform row the lanes point at."""
    existing = await Platform.objects.filter(platform_type="vk").first()
    if existing is not None:
        yield existing
        return
    created = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type="vk",
        base_url="https://vk.com",
        params={},
    )
    yield created
    await Platform.objects.delete_by_id(created.id)


@pytest.fixture
async def task(workspace):
    """A collecting task in the workspace, without any linked source."""
    with tenant_scope(workspace.id), operator_permission_scope():
        created = await AgentTask.objects.create(
            name=f"mention-{secrets.token_hex(3)}",
            cron_expr="0 9 * * *",
            job_type="collect",
            payload={},
            is_active=True,
            next_run_at=None,
        )
    yield created
    with tenant_scope(workspace.id), operator_permission_scope():
        await AgentTask.objects.delete_by_id(created.id)


async def _make_task(workspace, payload: dict[str, Any]) -> AgentTask:
    """A collecting task that carries the given payload from the start."""
    with tenant_scope(workspace.id), operator_permission_scope():
        return await AgentTask.objects.create(
            name=f"mention-{secrets.token_hex(3)}",
            cron_expr="0 9 * * *",
            job_type="collect",
            payload=payload,
            is_active=True,
            next_run_at=None,
        )


async def _load(task_id: int):
    """The task with its linked sources — the step reads the links from it."""
    return await AgentTask.objects.filter(id=task_id).prefetch_related("sources", "sources.platform").first()


def _payload(**collection: Any) -> dict[str, Any]:
    """A task payload whose collection block carries the given overrides."""
    block: dict[str, Any] = {"limit": 50}
    block.update(collection)
    return {"collection": block}


async def _run_step(task, payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    from app.jobs.handlers import _run_mention_lanes_step

    return await _run_mention_lanes_step(task, task_payload=payload, dry_run=dry_run)


@pytest.mark.tenancy
async def test_a_phrase_without_a_lane_gets_a_task_owned_lane(workspace, task, platform) -> None:
    payload = _payload(queries=["доставка"])
    spec = query_source_spec(task_id=task.id, platform="vk", query="доставка")

    with tenant_scope(workspace.id), operator_permission_scope():
        summary = await _run_step(await _load(task.id), payload)

        assert summary["ran"] is True and summary["dry_run"] is False
        assert summary["created"] == 1 and summary["reused"] == 0 and summary["linked"] == 1
        assert summary["refused"] == [] and summary["missing"] == []
        assert "создано веток 1" in summary["note"]
        lanes = await Source.objects.filter(owner_task_id=task.id)
        assert len(lanes) == 1
        lane = lanes[0]
        assert lane.source_type == SourceType.SEARCH
        assert lane.external_id == spec.external_id
        assert lane.external_id.startswith(f"task-{task.id}-vk-"), "the id is derived from the numeric task owner"
        assert lane.owner_task_id == task.id
        assert lane.params == {"query": "доставка", "mode": "auto"}
        assert lane.is_active is True
        assert await AgentTaskManager().get_sources(task.id) == [lane.id]


@pytest.mark.tenancy
async def test_a_second_run_creates_no_duplicate_and_changes_no_link(workspace, task, platform) -> None:
    payload = _payload(queries=["доставка"])

    with tenant_scope(workspace.id), operator_permission_scope():
        first = await _run_step(await _load(task.id), payload)
        second = await _run_step(await _load(task.id), payload)

        assert first["created"] == 1
        assert second["created"] == 0, "a re-run creates no duplicate lane"
        assert second["reused"] == 1
        assert second["linked"] == 0, "a steady-state re-run writes no link"
        lanes = await Source.objects.filter(owner_task_id=task.id)
        assert len(lanes) == 1
        assert await AgentTaskManager().get_sources(task.id) == [lanes[0].id]


@pytest.mark.tenancy
async def test_a_lane_owned_by_another_task_is_refused_not_taken(workspace, task, platform) -> None:
    payload = _payload(queries=["доставка"])
    spec = query_source_spec(task_id=task.id, platform="vk", query="доставка")

    with tenant_scope(workspace.id), operator_permission_scope():
        other = await AgentTask.objects.create(
            name=f"other-{secrets.token_hex(3)}",
            cron_expr="0 9 * * *",
            job_type="collect",
            payload={},
            is_active=True,
            next_run_at=None,
        )
        try:
            foreign = await Source.objects.create(
                platform_id=platform.id,
                name=spec.name,
                source_type=SourceType.SEARCH,
                external_id=spec.external_id,
                params={"query": "доставка", "mode": "user"},
                owner_task_id=other.id,
                is_active=True,
            )

            summary = await _run_step(await _load(task.id), payload)

            assert summary["created"] == 0 and summary["linked"] == 0
            assert len(summary["refused"]) == 1
            assert "belongs to task" in summary["refused"][0]
            assert "пропущено" in summary["note"], "the note reports the skipped lane"
            assert await Source.objects.filter(owner_task_id=task.id) == []
            row = await Source.objects.get(id=foreign.id)
            assert row.owner_task_id == other.id, "another task's lane is never taken over"
            assert row.params == {"query": "доставка", "mode": "user"}
            assert row.is_active is True
            assert foreign.id not in await AgentTaskManager().get_sources(task.id)
        finally:
            await AgentTask.objects.delete_by_id(other.id)


@pytest.mark.tenancy
async def test_all_mode_produces_one_joined_lane(workspace, task, platform) -> None:
    payload = _payload(queries=["доставка", "пицца"], queries_match="all")

    with tenant_scope(workspace.id), operator_permission_scope():
        summary = await _run_step(await _load(task.id), payload)

        assert summary["total"] == 1
        assert summary["queries"] == ["доставка пицца"]
        assert summary["created"] == 1
        lanes = await Source.objects.filter(owner_task_id=task.id)
        assert len(lanes) == 1
        assert lanes[0].params == {"query": "доставка пицца", "mode": "auto"}


@pytest.mark.tenancy
async def test_any_mode_produces_one_lane_per_phrase(workspace, task, platform) -> None:
    payload = _payload(queries=["доставка", "пицца"], queries_match="any")

    with tenant_scope(workspace.id), operator_permission_scope():
        summary = await _run_step(await _load(task.id), payload)

        assert summary["total"] == 2
        assert summary["created"] == 2
        lanes = await Source.objects.filter(owner_task_id=task.id)
        assert sorted(row.params["query"] for row in lanes) == ["доставка", "пицца"]
        assert sorted(await AgentTaskManager().get_sources(task.id)) == sorted(row.id for row in lanes)


@pytest.mark.tenancy
async def test_a_dry_run_creates_nothing_and_reports_the_missing_phrases(workspace, task, platform) -> None:
    payload = _payload(queries=["доставка", "пицца"])

    with tenant_scope(workspace.id), operator_permission_scope():
        summary = await _run_step(await _load(task.id), payload, dry_run=True)

        assert summary["dry_run"] is True
        assert summary["created"] == 0 and summary["linked"] == 0
        assert summary["missing"] == ["доставка", "пицца"]
        assert "сухой прогон" in summary["note"]
        assert await Source.objects.filter(owner_task_id=task.id) == []
        assert await AgentTaskManager().get_sources(task.id) == []


# --- per-source evidence rows (real database) ---------------------------------


@pytest.fixture
async def wall_source(workspace, platform):
    """A wall source linked to nothing until a test links it to its task."""
    with tenant_scope(workspace.id), operator_permission_scope():
        created = await Source.objects.create(
            platform_id=platform.id,
            name=f"wall-{secrets.token_hex(3)}",
            source_type=SourceType.CHANNEL,
            external_id=f"wall-{secrets.token_hex(4)}",
            is_active=True,
        )
    yield created
    with tenant_scope(workspace.id), operator_permission_scope():
        await Source.objects.delete_by_id(created.id)


def _double_collector(monkeypatch: pytest.MonkeyPatch, outcome: dict[str, Any] | BaseException) -> None:
    """Replace the collector's per-source entry point; no provider call."""

    async def fake_collect(self, source, **kwargs):  # noqa: ARG001 - handler call shape
        if isinstance(outcome, BaseException):
            raise outcome
        return dict(outcome)

    monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.collect_from_source", fake_collect)


@pytest.mark.tenancy
async def test_a_collected_wall_source_writes_one_evidence_row(workspace, task, wall_source, monkeypatch) -> None:
    from app.jobs.handlers import handle_collect

    _double_collector(
        monkeypatch,
        {"content_count": 3, "new_items": 3, "selected": 2, "needs_review": 1, "rejected": 1},
    )
    with tenant_scope(workspace.id), operator_permission_scope():
        await AgentTaskManager().update_by_id(task.id, payload={"collection": {"limit": 2}})
        await AgentTaskManager().set_sources(task.id, [wall_source.id])

        stats = await handle_collect({"agent_task_id": task.id, "job_id": 901})

        assert stats["per_source"][0]["items"] == 3
        rows = await CollectionLaneRun.objects.filter(task_id=task.id)
        assert len(rows) == 1, "one evidence row per collected source"
        row = rows[0]
        assert row.lane_id == f"wall:vk:{wall_source.id}"
        assert row.lane_type == "wall_posts"
        assert row.query is None
        assert row.fetched == 3
        assert row.selected == 2 and row.needs_review == 1 and row.rejected == 1
        assert row.limit_count == 2, "the bound is the task's collection limit"
        assert row.limit_reached is True
        assert row.error_code is None and row.unsupported_reason is None
        assert row.job_id == 901
        assert row.started_at is not None and row.finished_at is not None


@pytest.mark.tenancy
async def test_a_failed_source_writes_a_bounded_error_code(workspace, wall_source, monkeypatch) -> None:
    from app.jobs.handlers import handle_collect

    _double_collector(
        monkeypatch,
        RuntimeError("Ошибка API (1051): This method is disabled for your application type"),
    )
    with tenant_scope(workspace.id), operator_permission_scope():
        task = await _make_task(workspace, {"collection": {"limit": 2}})
        try:
            await AgentTaskManager().set_sources(task.id, [wall_source.id])

            stats = await handle_collect({"agent_task_id": task.id})

            assert stats["error"] == 1
            assert stats["per_source"][0]["error_code"] == "api_1051"
            rows = await CollectionLaneRun.objects.filter(task_id=task.id)
            assert len(rows) == 1, "a failed source is evidence too"
            row = rows[0]
            assert row.lane_id == f"wall:vk:{wall_source.id}"
            assert row.lane_type == "wall_posts"
            assert row.fetched == 0
            assert row.limit_count == 2 and row.limit_reached is False
            assert row.error_code == "api_1051", "the bounded provider code, never raw text"
            assert row.unsupported_reason == "метод недоступен для типа приложения"
            for field in (row.error_code, row.unsupported_reason, row.query, row.lane_id, row.lane_type):
                assert "application type" not in (field or "")
            assert len(row.error_code) <= 50
            assert len(row.unsupported_reason or "") <= 255
        finally:
            await AgentTask.objects.delete_by_id(task.id)


@pytest.mark.tenancy
async def test_a_created_search_lane_is_collected_and_reported_in_the_same_run(
    workspace, platform, monkeypatch
) -> None:
    from app.jobs.handlers import handle_collect

    _double_collector(
        monkeypatch,
        {"content_count": 4, "new_items": 4, "selected": 4, "needs_review": 0, "rejected": 0},
    )
    with tenant_scope(workspace.id), operator_permission_scope():
        task = await _make_task(workspace, {"collection": {"queries": ["доставка пицца"], "limit": 5}})
        try:
            stats = await handle_collect({"agent_task_id": task.id})

            mention = stats["mention_lanes"]
            assert mention["ran"] is True and mention["created"] == 1 and mention["reused"] == 0
            lanes = await Source.objects.filter(owner_task_id=task.id)
            assert len(lanes) == 1
            lane = lanes[0]
            assert lane.owner_task_id == task.id
            assert lane.params == {"query": "доставка пицца", "mode": "auto"}
            assert stats["sources"] == 1
            assert stats["per_source"][0]["source_id"] == lane.id
            rows = await CollectionLaneRun.objects.filter(task_id=task.id)
            assert len(rows) == 1
            row = rows[0]
            assert row.lane_id == f"search:vk:source:{lane.id}"
            assert row.lane_type == "post_search"
            assert row.query == "доставка пицца"
            assert row.fetched == 4 and row.selected == 4
            assert row.limit_count == 5 and row.limit_reached is False
            assert row.error_code is None
        finally:
            await AgentTask.objects.delete_by_id(task.id)
