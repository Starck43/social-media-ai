"""Import of a pasted VK list into a task's wall lanes.

The import resolves every pasted entry with the read methods that still work
(`groups.getById`, `users.get` — `newsfeed.search`/`groups.search` answer
error 1051 for this app profile type) and turns each hit into a task-owned
wall lane. The contract these tests pin:

1. parsing accepts screen names, signed ids, vk.com prefixes and URLs, and
   binds the kind the entry itself declares;
2. resolution is mocked: community first, person second, an unknown entry is
   reported and the batch continues;
3. planning is pure: reuse of a workspace source with the same screen name or
   numeric id, creation for the rest, refusal of another task's lane, and the
   same decisions on a re-run;
4. applying is the only writer: idempotent, links through the shared manager,
   never deletes anything and never touches another task's source.

The first three groups run without a database; the last one runs against the
real database and is marked `@pytest.mark.tenancy`.
"""

from __future__ import annotations

import secrets
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Platform, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.tenant_manager import tenants
from app.services.collection.lane_import import (
    KIND_COMMUNITY,
    KIND_PERSON,
    LaneMigrationError,
    ResolvedTarget,
    apply_lane_import,
    parse_lane_entry,
    plan_lane_import,
    resolve_lane_targets,
)
from app.types import SourceType
from tests.shared.platform_catalog import shared_platform

# --- parsing (no database) --------------------------------------------------


def test_parse_reads_a_plain_screen_name() -> None:
    entry = parse_lane_entry("dreviz2000")
    assert entry.value == "dreviz2000"
    assert entry.kind is None, "a plain screen name resolves as a community first, then as a person"
    assert entry.literal_id is None


def test_parse_keeps_a_negative_id_as_a_community() -> None:
    entry = parse_lane_entry("-113075242")
    assert entry.kind == KIND_COMMUNITY
    assert entry.literal_id == 113075242
    assert entry.value == "113075242", "VK wants a positive group_id"


def test_parse_resolves_a_positive_id_by_the_forced_kind() -> None:
    assert parse_lane_entry("113075242", kind=KIND_PERSON).kind == KIND_PERSON
    assert parse_lane_entry("113075242", kind=KIND_COMMUNITY).kind == KIND_COMMUNITY
    assert parse_lane_entry("113075242").kind is None


def test_parse_reads_the_community_prefixes() -> None:
    for raw in ("club113075242", "public113075242", "event123"):
        entry = parse_lane_entry(raw)
        assert entry.kind == KIND_COMMUNITY, raw
        assert entry.value.lstrip("-").isdigit(), raw
        assert entry.literal_id == int(entry.value), raw


def test_parse_reads_the_id_prefix_as_a_person() -> None:
    entry = parse_lane_entry("id12345")
    assert entry.kind == KIND_PERSON
    assert entry.value == "12345"
    assert entry.literal_id == 12345


def test_parse_reads_vk_urls() -> None:
    expected = {
        "https://vk.com/club113075242": ("113075242", KIND_COMMUNITY),
        "vk.com/public113075242": ("113075242", KIND_COMMUNITY),
        "https://vk.com/dreviz2000?w=wall-113075242_1": ("dreviz2000", None),
        "https://m.vk.com/dreviz2000#/about": ("dreviz2000", None),
        "https://vk.ru/id12345": ("12345", KIND_PERSON),
    }
    for raw, (value, kind) in expected.items():
        entry = parse_lane_entry(raw)
        assert entry.raw == raw
        assert (entry.value, entry.kind) == (value, kind), raw


def test_parse_does_not_mistake_a_prefix_like_screen_name_for_an_id() -> None:
    entry = parse_lane_entry("idea_club")
    assert entry.value == "idea_club"
    assert entry.kind is None, "the remainder is not all digits, so it stays a screen name"


def test_parse_refuses_contradicting_kinds() -> None:
    with pytest.raises(LaneMigrationError, match="community id"):
        parse_lane_entry("-113075242", kind=KIND_PERSON)
    with pytest.raises(LaneMigrationError, match="not a person"):
        parse_lane_entry("club113", kind=KIND_PERSON)


def test_parse_refuses_an_unknown_kind_and_an_empty_entry() -> None:
    with pytest.raises(LaneMigrationError, match="unknown kind"):
        parse_lane_entry("dreviz2000", kind="group")
    with pytest.raises(LaneMigrationError, match="empty entry"):
        parse_lane_entry("   ")


# --- resolution (mocked VK, no database) ------------------------------------


class _PlatformRow:
    """The platform row as the resolver reads it (no database)."""

    platform_type = "vk"
    params = {"api_version": "5.199"}


def _fake_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import Platform

    manager = SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(first=AsyncMock(return_value=_PlatformRow())))
    monkeypatch.setattr(Platform, "objects", manager, raising=False)


def _fake_client(monkeypatch: pytest.MonkeyPatch, group, user) -> SimpleNamespace:
    from app.services.social import factory

    client = SimpleNamespace(resolve_group=AsyncMock(return_value=group), resolve_user=AsyncMock(return_value=user))
    monkeypatch.setattr(factory, "get_social_client", lambda platform: client)
    return client


async def test_resolution_tries_a_community_before_a_person(monkeypatch) -> None:
    _fake_platform(monkeypatch)
    client = _fake_client(
        monkeypatch,
        group={"id": -113075242, "screen_name": "dreviz2000", "name": "Древиз", "is_closed": False},
        user=None,
    )

    targets = await resolve_lane_targets([parse_lane_entry("dreviz2000")], platform="vk")

    client.resolve_group.assert_awaited_once_with("dreviz2000")
    client.resolve_user.assert_not_awaited()
    assert targets[0].kind == KIND_COMMUNITY
    assert targets[0].external_id == "-113075242"
    assert targets[0].numeric_id == -113075242
    assert targets[0].name == "Древиз"


async def test_resolution_falls_back_to_a_person(monkeypatch) -> None:
    _fake_platform(monkeypatch)
    client = _fake_client(monkeypatch, group=None, user={"id": 12345, "screen_name": "durov", "name": "Павел Дуров"})

    targets = await resolve_lane_targets([parse_lane_entry("durov")], platform="vk")

    client.resolve_user.assert_awaited_once_with("durov")
    assert targets[0].kind == KIND_PERSON
    assert targets[0].external_id == "12345"
    assert targets[0].numeric_id == 12345


async def test_resolution_reports_an_unresolvable_entry(monkeypatch) -> None:
    _fake_platform(monkeypatch)
    _fake_client(monkeypatch, group=None, user=None)

    targets = await resolve_lane_targets(
        [parse_lane_entry("ghost", kind=KIND_PERSON), parse_lane_entry("ghost2", kind=KIND_COMMUNITY)],
        platform="vk",
    )

    assert targets[0].error == "vk has no such person"
    assert targets[1].error == "vk has no such community"
    assert all(target.external_id == "" for target in targets)


async def test_resolution_forces_the_kind_when_the_entry_binds_none(monkeypatch) -> None:
    _fake_platform(monkeypatch)
    client = _fake_client(
        monkeypatch,
        group={"id": -113, "screen_name": "dreviz2000", "name": "Древиз", "is_closed": False},
        user=None,
    )

    targets = await resolve_lane_targets([parse_lane_entry("dreviz2000")], platform="vk", kind=KIND_COMMUNITY)
    client.resolve_group.assert_awaited_once()
    assert targets[0].kind == KIND_COMMUNITY

    client.resolve_group.reset_mock()
    await resolve_lane_targets([parse_lane_entry("dreviz2000")], platform="vk", kind=KIND_PERSON)
    client.resolve_group.assert_not_awaited()


async def test_resolution_propagates_a_token_error(monkeypatch) -> None:
    from app.services.social.credentials import AuthorizationRequired

    _fake_platform(monkeypatch)
    client = _fake_client(monkeypatch, group=None, user=None)
    client.resolve_group.side_effect = AuthorizationRequired("Нужен токен ВКонтакте", platform="vk")

    with pytest.raises(AuthorizationRequired):
        await resolve_lane_targets([parse_lane_entry("dreviz2000")], platform="vk")


async def test_resolution_dedupes_identical_entries(monkeypatch) -> None:
    _fake_platform(monkeypatch)
    client = _fake_client(
        monkeypatch,
        group={"id": -113, "screen_name": "dreviz2000", "name": "Древиз", "is_closed": False},
        user=None,
    )

    targets = await resolve_lane_targets(
        [parse_lane_entry("dreviz2000"), parse_lane_entry("https://vk.com/dreviz2000")],
        platform="vk",
    )

    client.resolve_group.assert_awaited_once()
    assert len(targets) == 1


# --- planning (no database) -------------------------------------------------


def _task(**overrides) -> SimpleNamespace:
    """A task row as the planner sees it: identity, linked sources."""
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
    source_type: str = "GROUP",
    external_id: str = "-113075242",
    owner_task_id: int | None = None,
    platform: str = "vk",
) -> SimpleNamespace:
    """A workspace source row as the planner sees it."""
    return SimpleNamespace(
        id=source_id,
        platform=SimpleNamespace(platform_type=platform),
        source_type=source_type,
        external_id=external_id,
        owner_task_id=owner_task_id,
    )


def _community(**overrides) -> ResolvedTarget:
    data = dict(
        raw="https://vk.com/dreviz2000",
        kind=KIND_COMMUNITY,
        external_id="-113075242",
        numeric_id=-113075242,
        screen_name="dreviz2000",
        name="Древиз",
    )
    data.update(overrides)
    return ResolvedTarget(**data)


def _person(**overrides) -> ResolvedTarget:
    data = dict(
        raw="id12345",
        kind=KIND_PERSON,
        external_id="12345",
        numeric_id=12345,
        screen_name=None,
        name="Иван Иванов",
    )
    data.update(overrides)
    return ResolvedTarget(**data)


def _plan(task, targets, sources=()):
    """Plan with the CLI's prefetch shape: the workspace's VK rows."""
    return plan_lane_import(task, targets, platform="vk", workspace_sources=list(sources))


def test_plan_creates_a_task_owned_lane_for_a_new_target() -> None:
    plan = _plan(_task(), [_community(), _person()])

    assert [decision.action for decision in plan.decisions] == ["create", "create"]
    community, person = plan.decisions
    assert community.source_type == "GROUP"
    assert community.external_id == "-113075242"
    assert community.name == "Древиз"
    assert person.source_type == "USER"
    assert person.external_id == "12345"
    assert plan.linked_source_ids == []


def test_plan_reuses_a_workspace_source_by_screen_name() -> None:
    existing = _source(11, external_id="dreviz2000", owner_task_id=None)

    plan = _plan(_task(), [_community()], [existing])

    [decision] = plan.decisions
    assert decision.action == "reuse"
    assert decision.source_id == 11
    assert plan.linked_source_ids == [11]


def test_plan_reuses_a_workspace_source_by_numeric_id() -> None:
    numeric = _source(12, external_id="-113075242", owner_task_id=None)
    legacy_prefix = _source(13, external_id="club113075242", owner_task_id=None)

    plan = _plan(_task(), [_community()], [legacy_prefix])
    assert plan.decisions[0].action == "reuse"
    assert plan.decisions[0].source_id == 13

    plan = _plan(_task(), [_community()], [numeric])
    assert plan.decisions[0].action == "reuse"
    assert plan.decisions[0].source_id == 12


def test_plan_prefers_the_numeric_row_over_a_screen_name_row() -> None:
    by_name = _source(14, external_id="dreviz2000", owner_task_id=None)
    by_id = _source(15, external_id="-113075242", owner_task_id=None)

    plan = _plan(_task(), [_community()], [by_name, by_id])

    [decision] = plan.decisions
    assert decision.action == "reuse"
    assert decision.source_id == 15


def test_plan_never_matches_a_mistyped_row() -> None:
    person_row = _source(16, source_type="USER", external_id="113075242", owner_task_id=None)

    plan = _plan(_task(), [_community()], [person_row])

    [decision] = plan.decisions
    assert decision.action == "create", "a person-typed row with the same digits is not a community lane"


def test_plan_reuses_a_source_owned_by_the_same_task() -> None:
    mine = _source(18, external_id="-113075242", owner_task_id=7)

    plan = _plan(_task(sources=[mine]), [_community()], [mine])

    [decision] = plan.decisions
    assert decision.action == "reuse"
    assert decision.source_id == 18


def test_plan_refuses_a_source_owned_by_another_task() -> None:
    foreign = _source(19, external_id="-113075242", owner_task_id=9)

    plan = _plan(_task(), [_community()], [foreign])

    [decision] = plan.decisions
    assert decision.action == "skip"
    assert "task 9" in decision.reason
    assert plan.linked_source_ids == []


def test_plan_refuses_to_guess_between_two_numeric_candidates() -> None:
    rows = [
        _source(20, external_id="-113075242", owner_task_id=None),
        _source(21, external_id="club113075242", owner_task_id=None),
    ]

    plan = _plan(_task(), [_community()], rows)

    [decision] = plan.decisions
    assert decision.action == "skip"
    assert "several sources match" in decision.reason


def test_plan_skips_an_unresolvable_target_with_its_reason() -> None:
    unresolved = _community(
        raw="ghost",
        kind="",
        external_id="",
        numeric_id=0,
        screen_name=None,
        name=None,
        error="vk has no such community or person",
    )

    plan = _plan(_task(), [unresolved])

    [decision] = plan.decisions
    assert decision.action == "skip"
    assert decision.reason == "vk has no such community or person"
    assert plan.linked_source_ids == []


def test_plan_run_twice_yields_the_same_decisions() -> None:
    """After the first apply the created lanes are the task's own; the second
    run must re-decide reuse for exactly them, never create again."""
    created = [
        _source(71, source_type="GROUP", external_id="-113075242", owner_task_id=7),
        _source(72, source_type="USER", external_id="12345", owner_task_id=7),
    ]
    owned_task = _task(sources=created)

    first = _plan(_task(), [_community(), _person()])
    second = _plan(owned_task, [_community(), _person()], created)

    assert [decision.action for decision in first.decisions] == ["create", "create"]
    assert [decision.action for decision in second.decisions] == ["reuse", "reuse"]
    assert [decision.source_id for decision in second.decisions] == [71, 72]
    assert second.linked_source_ids == [71, 72]


# --- applying (real database) -----------------------------------------------


@pytest.fixture
async def workspace():
    """A fresh workspace so the tenant guard is exercised for real."""
    tenant = await tenants.create(name="Lane import", slug=f"laneimport{secrets.token_hex(4)}")
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
            name=f"import-{secrets.token_hex(3)}",
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


async def _planned(task_row, targets, platform, *, prefetch: bool = True):
    """Plan as the CLI does: the workspace's VK rows are the reuse candidates."""
    rows = []
    if prefetch:
        rows = await Source.objects.filter(
            tenant_id=task_row.tenant_id,
            platform_id=platform.id,
        ).select_related("platform")
    return plan_lane_import(task_row, targets, platform="vk", workspace_sources=list(rows))


@pytest.mark.tenancy
async def test_apply_creates_task_owned_wall_lanes(workspace, task, platform) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        plan = await _planned(await _load(task.id), [_community(), _person()], platform)

        result = await apply_lane_import(plan)

        assert len(result.created_source_ids) == 2
        assert result.refused == []
        rows = await Source.objects.filter(owner_task_id=task.id)
        by_external = {row.external_id: row for row in rows}
        assert set(by_external) == {"-113075242", "12345"}
        community, person = by_external["-113075242"], by_external["12345"]
        assert community.source_type == SourceType.GROUP
        assert person.source_type == SourceType.USER
        for row in (community, person):
            assert row.owner_task_id == task.id
            assert row.params == {"mode": "auto"}, "the L1->L2 fallback then applies"
            assert row.is_active is True
        assert set(await AgentTaskManager().get_sources(task.id)) == {community.id, person.id}


@pytest.mark.tenancy
async def test_a_second_apply_creates_nothing_new(workspace, task, platform) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        plan = await _planned(await _load(task.id), [_community()], platform)

        first = await apply_lane_import(plan)
        second = await apply_lane_import(plan)

        assert first.created_source_ids
        assert second.created_source_ids == [], "the row is found by (workspace, platform, external_id)"
        assert second.reused_source_ids == first.created_source_ids
        assert second.linked_source_ids == first.linked_source_ids
        assert len(await Source.objects.filter(owner_task_id=task.id)) == 1


@pytest.mark.tenancy
async def test_apply_reuses_an_existing_workspace_source_untouched(workspace, task, platform) -> None:
    with tenant_scope(workspace.id), operator_permission_scope():
        existing = await Source.objects.create(
            platform_id=platform.id,
            name="Древиз каталог",
            source_type=SourceType.GROUP,
            external_id="dreviz2000",
            params={"mode": "user"},
            is_active=True,
        )
        plan = await _planned(await _load(task.id), [_community()], platform)
        [decision] = plan.decisions
        assert decision.action == "reuse"
        assert decision.source_id == existing.id

        result = await apply_lane_import(plan)

        assert result.created_source_ids == []
        assert result.reused_source_ids == [existing.id]
        row = await Source.objects.get(id=existing.id)
        assert row.name == "Древиз каталог", "reuse never retitles a source"
        assert row.source_type == SourceType.GROUP
        assert row.params == {"mode": "user"}, "reuse never retunes a source"
        assert row.owner_task_id is None, "a workspace source stays workspace-owned"
        assert row.is_active is True
        assert existing.id in await AgentTaskManager().get_sources(task.id)


@pytest.mark.tenancy
async def test_apply_refuses_a_source_owned_by_another_task(workspace, task, platform) -> None:
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
            name="Древиз",
            source_type=SourceType.GROUP,
            external_id="-113075242",
            params={"mode": "user"},
            owner_task_id=other.id,
            is_active=True,
        )
        # The prefetch missed the row (e.g. created after the plan), so the
        # plan still says create — the apply must catch the owner itself.
        plan = await _planned(await _load(task.id), [_community()], platform, prefetch=False)

        result = await apply_lane_import(plan)

        assert result.created_source_ids == []
        assert result.refused and "belongs to task" in result.refused[0]
        row = await Source.objects.get(id=foreign.id)
        assert row.owner_task_id == other.id, "another task's lane is never taken over"
        assert row.params == {"mode": "user"}
        assert row.is_active is True
        assert row.id not in await AgentTaskManager().get_sources(task.id)
