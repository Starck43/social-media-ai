"""Population step: the task's own search resolved into task-owned lanes.

The contract these tests pin:

1. the per-phrase search results combine by the intent's `queries_match`:
   `any` is the union, `all` is the intersection by entity id;
2. an empty discovery declaration is a no-op — no provider call, no write;
3. the declared city id reaches the search call unchanged, and a bare city
   name is resolved once (an unresolvable name is `population_city_unknown`,
   never a city-less search);
4. a provider failure is a NAMED PopulationError (`api_1051`,
   `population_auth_required`), never "nothing found";
5. a provider that cannot search a kind is not called — the gap is reported;
6. at the database, every found entity becomes a task-owned lane through the
   shared lane-import rules (reuse of an existing workspace source, refusal of
   another task's lane, idempotent re-runs), and a dry run writes nothing;
7. the step writes one `collection_lane_runs` evidence row per run with the
   counts, the bound and — when blocked — the error code.

Provider calls are mocked in every test; no test touches the network. The
group 6–7 tests run against the real database and are `pytest.mark.tenancy`.
"""

from __future__ import annotations

import secrets
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, CollectionLaneRun, Platform, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import tenants
from app.services.collection.intent import CollectionIntent, parse_collection_intent
from app.services.collection.population import PopulationError, resolve_population
from app.types import SourceType
from tests.shared.platform_catalog import shared_platform

# --- shared fakes (no database) ----------------------------------------------


def _intent(**discovery_overrides: Any) -> CollectionIntent:
    discovery: dict[str, Any] = {
        "people": True,
        "communities": True,
        "queries": ["дизайн интерьера"],
    }
    discovery.update(discovery_overrides)
    return parse_collection_intent({"collection": {"discovery": discovery}})


def _task(*, task_id: int = 7, tenant_id: int = 3, sources: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=task_id, tenant_id=tenant_id, name="Поиск популяции", sources=list(sources or []))


def _group_row(number: int, name: str = "Древиз", screen_name: str | None = None) -> dict:
    row = {"external_id": str(-abs(number)), "name": name}
    if screen_name:
        row["screen_name"] = screen_name
    return row


def _user_row(number: int, name: str = "Иван Иванов") -> dict:
    return {"external_id": str(number), "name": name}


def _client(*, groups: list | None = None, users: list | None = None, cities: list | None = None):
    return SimpleNamespace(
        search_groups=AsyncMock(return_value=list(groups or [])),
        search_users=AsyncMock(return_value=list(users or [])),
        get_cities=AsyncMock(return_value=list(cities or [])),
    )


def _patch_provider(monkeypatch: pytest.MonkeyPatch, client, *, platform_key: str = "vk") -> Mock:
    """The platform row and the client factory, as the runtime sees them."""
    from app.models import Platform
    from app.services.social import factory
    from app.services.social import owner as owner_module

    row = SimpleNamespace(id=1, platform_type=platform_key, params={"api_version": "5.199"})
    platform_filter = Mock(return_value=SimpleNamespace(first=AsyncMock(return_value=row)))
    monkeypatch.setattr(Platform, "objects", SimpleNamespace(filter=platform_filter), raising=False)

    async def _no_owner(source):
        return None

    monkeypatch.setattr(factory, "get_social_client", lambda platform: client)
    monkeypatch.setattr(owner_module, "resolve_source_owner", _no_owner)
    return platform_filter


def _patch_workspace_sources(monkeypatch: pytest.MonkeyPatch, rows=()) -> None:
    from app.models import Source

    async def _read():
        return list(rows)

    manager = SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(select_related=lambda *_args: _read()))
    monkeypatch.setattr(Source, "objects", manager, raising=False)


def _source_row(source_id: int, external_id: str, owner_task_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=source_id,
        platform=SimpleNamespace(platform_type="vk"),
        source_type="GROUP",
        external_id=external_id,
        owner_task_id=owner_task_id,
    )


async def _resolve(task, intent, *, platform: str = "vk", dry_run: bool = True, job_id: int | None = None):
    return await resolve_population(task=task, intent=intent, platform=platform, dry_run=dry_run, job_id=job_id)


# --- phrase combination (no database) ----------------------------------------


def _two_phrase_client() -> SimpleNamespace:
    client = _client()

    async def _groups(query, **kwargs):
        if query == "дизайн интерьера":
            return [_group_row(101), _group_row(102)]
        if query == "дизайнеры":
            return [_group_row(102), _group_row(103)]
        raise AssertionError(f"unexpected query {query!r}")

    client.search_groups.side_effect = _groups
    return client


async def test_any_mode_unions_the_per_phrase_results(monkeypatch) -> None:
    client = _two_phrase_client()
    _patch_provider(monkeypatch, client)
    _patch_workspace_sources(monkeypatch)

    result = await _resolve(
        _task(),
        _intent(people=False, communities=True, queries=("дизайн интерьера", "дизайнеры"), queries_match="any"),
    )

    assert result.ran is True
    assert [entity.numeric_id for entity in result.entities] == [-101, -102, -103]
    assert [(phrase.communities, phrase.people) for phrase in result.per_phrase] == [(2, 0), (2, 0)]
    assert [entity.numeric_id for entity in result.would_create] == [-101, -102, -103]
    assert result.created_source_ids == () and result.reused_source_ids == ()
    client.search_users.assert_not_awaited()


async def test_all_mode_intersects_the_per_phrase_results_by_id(monkeypatch) -> None:
    client = _two_phrase_client()
    _patch_provider(monkeypatch, client)
    _patch_workspace_sources(monkeypatch)

    result = await _resolve(
        _task(),
        _intent(people=False, communities=True, queries=("дизайн интерьера", "дизайнеры"), queries_match="all"),
    )

    assert [entity.numeric_id for entity in result.entities] == [-102]
    assert [entity.numeric_id for entity in result.would_create] == [-102]


async def test_existing_workspace_source_is_reused_not_retyped(monkeypatch) -> None:
    client = _client(groups=[_group_row(101, screen_name="dreviz2000"), _group_row(102)])
    _patch_provider(monkeypatch, client)
    _patch_workspace_sources(monkeypatch, rows=[_source_row(11, "-101")])

    result = await _resolve(_task(), _intent(people=False, communities=True))

    assert [entity.numeric_id for entity in result.would_create] == [
        -102
    ], "the workspace row holding the identity is reused as-is, never recreated"
    assert result.warnings == ()


# --- no-op and city scoping (no database) ------------------------------------


async def test_empty_discovery_is_a_noop_without_provider_calls(monkeypatch) -> None:
    client = _client()
    platform_filter = _patch_provider(monkeypatch, client)

    plain = await _resolve(_task(), parse_collection_intent({}))
    queries_only = await _resolve(_task(), _intent(people=False, communities=False))

    assert plain.ran is False and plain.entities == ()
    assert queries_only.ran is False and queries_only.entities == ()
    client.search_groups.assert_not_awaited()
    client.search_users.assert_not_awaited()
    client.get_cities.assert_not_awaited()
    assert platform_filter.call_count == 0, "a no-op never reaches the provider"


async def test_declared_city_id_reaches_the_search_call(monkeypatch) -> None:
    client = _client(groups=[_group_row(101)])
    _patch_provider(monkeypatch, client)
    _patch_workspace_sources(monkeypatch)

    await _resolve(_task(), _intent(people=False, communities=True, city={"name": "Киров", "id": 47}))

    client.get_cities.assert_not_awaited()
    client.search_groups.assert_awaited_once_with("дизайн интерьера", owner_user_id=None, city_id=47, count=100)


async def test_a_bare_city_name_is_resolved_once(monkeypatch) -> None:
    client = _client(groups=[_group_row(101)], cities=[{"id": 47, "title": "Киров"}])
    _patch_provider(monkeypatch, client)
    _patch_workspace_sources(monkeypatch)

    result = await _resolve(_task(), _intent(people=False, communities=True, city={"name": "Киров"}))

    client.get_cities.assert_awaited_once_with("Киров", owner_user_id=None, count=5)
    client.search_groups.assert_awaited_once_with("дизайн интерьера", owner_user_id=None, city_id=47, count=100)
    assert result.city_id == 47 and result.city_name == "Киров"


async def test_an_unresolvable_city_is_reported_not_searched(monkeypatch) -> None:
    client = _client(cities=[])
    _patch_provider(monkeypatch, client)

    with pytest.raises(PopulationError) as exc:
        await _resolve(_task(), _intent(people=False, communities=True, city={"name": "Нигдеград"}))

    assert exc.value.code == "population_city_unknown"
    assert exc.value.hint, "the operator gets a bounded reason"
    client.search_groups.assert_not_awaited()
    client.search_users.assert_not_awaited()


# --- blocked searches stay named (no database) --------------------------------


async def test_a_vk_error_becomes_a_named_code_with_a_bounded_hint(monkeypatch) -> None:
    client = _client()
    client.search_groups.side_effect = RuntimeError(
        "Ошибка API (1051): This method is disabled for your application type"
    )
    _patch_provider(monkeypatch, client)

    with pytest.raises(PopulationError) as exc:
        await _resolve(_task(), _intent(people=False, communities=True))

    error = exc.value
    assert error.code == "api_1051"
    assert error.query == "дизайн интерьера"
    assert error.hint, "the operator gets a bounded reason"
    assert "application type" not in error.hint, "raw provider text never reaches the report"


async def test_a_missing_token_is_reported_not_swallowed(monkeypatch) -> None:
    from app.services.social.credentials import AuthorizationRequired

    client = _client()
    client.search_groups.side_effect = AuthorizationRequired(
        "Для поиска по ВКонтакте нужен личный токен", platform="vk"
    )
    _patch_provider(monkeypatch, client)

    with pytest.raises(PopulationError) as exc:
        await _resolve(_task(), _intent(people=False, communities=True))

    assert exc.value.code == "population_auth_required"


async def test_a_provider_that_cannot_search_is_not_called(monkeypatch) -> None:
    client = _client()

    with pytest.raises(PopulationError) as exc:
        await _resolve(_task(), _intent(), platform="telegram")

    assert exc.value.code == "population_search_unsupported"
    client.search_groups.assert_not_awaited()
    client.search_users.assert_not_awaited()
    client.get_cities.assert_not_awaited()


async def test_a_dry_run_reports_what_would_be_created(monkeypatch) -> None:
    client = _client(groups=[_group_row(101), _group_row(102)], users=[_user_row(501)])
    _patch_provider(monkeypatch, client)
    _patch_workspace_sources(monkeypatch)

    result = await _resolve(_task(), _intent(), dry_run=True)

    assert result.dry_run and result.ran
    assert len(result.entities) == 3
    assert {(entity.kind, entity.numeric_id) for entity in result.would_create} == {
        ("community", -101),
        ("community", -102),
        ("person", 501),
    }
    assert result.created_source_ids == () and result.reused_source_ids == ()
    assert result.linked_source_ids == ()


# --- population against the database -----------------------------------------


@pytest.fixture
async def workspace():
    tenant = await tenants.create(name="Population", slug=f"population{secrets.token_hex(4)}")
    yield tenant
    await tenants.delete_by_id(tenant.id)


@pytest.fixture
async def platform():
    existing = await Platform.objects.filter(platform_type="vk").first()
    if existing is not None:
        yield existing
        return
    created = await shared_platform("vk")
    yield created


@pytest.fixture
async def task(workspace):
    with tenant_scope(workspace.id), operator_permission_scope():
        created = await AgentTask.objects.create(
            name=f"population-{secrets.token_hex(3)}",
            cron_expr="0 9 * * *",
            job_type="collect",
            payload={},
            is_active=True,
            next_run_at=None,
        )
    yield created
    with tenant_scope(workspace.id), operator_permission_scope():
        await AgentTask.objects.delete_by_id(created.id)


def _patch_db_client(monkeypatch: pytest.MonkeyPatch, client) -> None:
    from app.services.social import factory

    monkeypatch.setattr(factory, "get_social_client", lambda platform: client)


async def _load(task_id: int):
    return await AgentTask.objects.filter(id=task_id).prefetch_related("sources", "sources.platform").first()


@pytest.mark.tenancy
async def test_population_creates_task_owned_lanes_and_links_the_task(workspace, task, platform, monkeypatch) -> None:
    client = _client(groups=[_group_row(113075242), _group_row(999)], users=[_user_row(12345)])
    _patch_db_client(monkeypatch, client)

    with tenant_scope(workspace.id), operator_permission_scope():
        result = await _resolve(await _load(task.id), _intent(), dry_run=False)

        assert result.ran and not result.dry_run
        assert len(result.created_source_ids) == 3
        assert result.reused_source_ids == ()
        assert result.refused == ()
        rows = await Source.objects.filter(owner_task_id=task.id)
        by_external = {row.external_id: row for row in rows}
        assert set(by_external) == {"-113075242", "-999", "12345"}
        assert by_external["-113075242"].source_type == SourceType.GROUP
        assert by_external["12345"].source_type == SourceType.USER
        for row in by_external.values():
            assert row.owner_task_id == task.id
            assert row.params == {"mode": "auto"}
            assert row.is_active is True
        linked = await AgentTaskManager().get_sources(task.id)
        assert set(result.linked_source_ids) == set(linked)
        assert set(linked) == {row.id for row in by_external.values()}


@pytest.mark.tenancy
async def test_a_second_run_reuses_the_same_lanes_without_duplicates(workspace, task, platform, monkeypatch) -> None:
    client = _client(groups=[_group_row(113075242)], users=[_user_row(12345)])
    _patch_db_client(monkeypatch, client)

    with tenant_scope(workspace.id), operator_permission_scope():
        first = await _resolve(await _load(task.id), _intent(), dry_run=False)
        second = await _resolve(await _load(task.id), _intent(), dry_run=False)

        assert first.created_source_ids
        assert second.created_source_ids == ()
        assert set(second.reused_source_ids) == set(first.created_source_ids)
        assert set(second.linked_source_ids) == set(first.linked_source_ids)
        assert len(await Source.objects.filter(owner_task_id=task.id)) == 2


@pytest.mark.tenancy
async def test_a_foreign_task_lane_is_refused_and_reported_not_taken(workspace, task, platform, monkeypatch) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        other = await AgentTask.objects.create(
            name=f"other-{secrets.token_hex(3)}",
            cron_expr="0 9 * * *",
            job_type="collect",
            payload={},
            is_active=True,
            next_run_at=None,
        )
        foreign = await Source.objects.create(
            platform_id=platform.id,
            name="Древиз чужая",
            source_type=SourceType.GROUP,
            external_id="-113075242",
            params={"mode": "user"},
            owner_task_id=other.id,
            is_active=True,
        )

    client = _client(groups=[_group_row(113075242)])
    _patch_db_client(monkeypatch, client)
    try:
        with tenant_scope(workspace.id), operator_permission_scope():
            result = await _resolve(await _load(task.id), _intent(people=False, communities=True), dry_run=False)

            assert result.created_source_ids == ()
            assert any("belongs to task" in warning for warning in result.warnings), result.warnings
            row = await Source.objects.get(id=foreign.id)
            assert row.owner_task_id == other.id, "another task's lane is never taken over"
            assert row.params == {"mode": "user"}
            assert row.is_active is True
            assert row.id not in await AgentTaskManager().get_sources(task.id)
    finally:
        with tenant_scope(workspace.id), operator_permission_scope():
            await AgentTask.objects.delete_by_id(other.id)


@pytest.mark.tenancy
async def test_a_dry_run_creates_nothing_and_reports_the_count(workspace, task, platform, monkeypatch) -> None:
    client = _client(groups=[_group_row(113075242), _group_row(999)], users=[_user_row(12345)])
    _patch_db_client(monkeypatch, client)

    with tenant_scope(workspace.id), operator_permission_scope():
        result = await _resolve(await _load(task.id), _intent(), dry_run=True)

        assert result.ran and result.dry_run
        assert len(result.entities) == 3
        assert len(result.would_create) == 3
        assert result.created_source_ids == () and result.linked_source_ids == ()
        assert len(await Source.objects.filter(owner_task_id=task.id)) == 0


@pytest.mark.tenancy
async def test_the_step_writes_lane_run_evidence(workspace, task, platform, monkeypatch) -> None:
    from app.jobs.handlers import _run_population_step

    client = _client(groups=[_group_row(113075242), _group_row(999)], users=[_user_row(12345)])
    _patch_db_client(monkeypatch, client)
    payload = {
        "collection": {
            "discovery": {
                "people": True,
                "communities": True,
                "queries": ["дизайн интерьера"],
                "city": {"name": "Киров", "id": 47},
            }
        }
    }

    with tenant_scope(workspace.id), operator_permission_scope():
        loaded = await _load(task.id)

        summary = await _run_population_step(loaded, task_payload=payload, dry_run=False, job_id=None)

        assert summary["ran"] is True
        assert summary["found"] == 3 and summary["created"] == 3
        assert summary["note"], "the run evidence carries the bounded note"
        rows = await CollectionLaneRun.objects.filter(task_id=task.id, lane_type="candidate_search")
        assert len(rows) == 1
        row = rows[0]
        assert row.lane_id == "population:vk"
        assert row.query == "дизайн интерьера"
        assert row.fetched == 3
        assert row.limit_count == 100
        assert row.limit_reached is False
        assert row.error_code is None and row.unsupported_reason is None
        assert row.started_at is not None and row.finished_at is not None

        client.search_groups.side_effect = RuntimeError(
            "Ошибка API (1051): This method is disabled for your application type"
        )
        blocked = await _run_population_step(loaded, task_payload=payload, dry_run=False, job_id=None)

        assert blocked["error_code"] == "api_1051"
        assert "api_1051" in blocked["note"], "the note names the reason"
        rows = await CollectionLaneRun.objects.filter(task_id=task.id, lane_type="candidate_search")
        assert len(rows) == 2, "one evidence row per run"
        failed = max(rows, key=lambda item: item.id)
        assert failed.error_code == "api_1051"
        assert failed.fetched == 0
        assert failed.query == "дизайн интерьера"
