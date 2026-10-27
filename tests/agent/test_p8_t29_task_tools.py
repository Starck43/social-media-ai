"""Focused contracts for P8-T29 social task planning tools.

All platform, source-write and launch boundaries are replaced with local
doubles. These tests must not call VK, enqueue work, or write the test database.
"""

from __future__ import annotations

import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

__source_only__ = True


def _module(monkeypatch, name: str, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_execute_once_is_unconfirmed_while_recurring_and_external_actions_stay_confirmed() -> None:
    from app.agent.tools import get_tool
    from app.agent.toolset import reports, tasks

    assert get_tool("task_execute_once").handler is tasks.task_execute_once
    assert get_tool("task_execute_once").confirm is False
    assert get_tool("task_add").confirm is True
    assert get_tool("digest_send_now").handler is reports.digest_send_now
    assert get_tool("digest_send_now").confirm is True
    job_type = get_tool("task_execute_once").parameters["properties"]["job_type"]
    assert job_type["enum"] == ["collect", "analyze"]
    assert job_type["default"] == "analyze"


async def test_execute_once_delegates_fixed_job_and_request_key(monkeypatch) -> None:
    from app.agent.toolset import tasks

    calls = []

    async def launch(plan, *, request_id, job_type):
        calls.append((plan, request_id, job_type))
        return {"status": "queued", "task_id": 31, "job_id": 41}

    _module(monkeypatch, "app.services.task_launch", launch_social_task=launch)
    plan = {"trigger": {"type": "once"}, "source_scope": "selected"}

    result = await tasks.task_execute_once("request-123", plan)

    assert result == {"status": "queued", "task_id": 31, "job_id": 41}
    assert calls == [(plan, "request-123", "analyze")]
    assert await tasks.task_execute_once("short", plan) == {
        "error": "The task launch request is invalid.",
        "code": "request_invalid",
    }
    assert len(calls) == 1


async def test_execute_once_rejects_missing_and_extra_arguments_before_launch(monkeypatch) -> None:
    from app.agent.toolset import tasks

    calls = []

    async def launch(*_args, **_kwargs):
        calls.append(True)
        return {"status": "queued"}

    _module(monkeypatch, "app.services.task_launch", launch_social_task=launch)

    assert await tasks.task_execute_once() == {
        "error": "The task launch request is invalid.",
        "code": "request_invalid",
    }
    assert await tasks.task_execute_once("request-123", {}, tenant_id=77) == {
        "error": "The task launch request is invalid.",
        "code": "request_invalid",
    }
    assert calls == []


async def test_execute_once_surfaces_a_replayed_request(monkeypatch) -> None:
    from app.agent.toolset import tasks

    async def replay(plan, *, request_id, job_type):
        return {"status": "replayed", "task_id": 31, "job_id": 41}

    _module(monkeypatch, "app.services.task_launch", launch_social_task=replay)
    assert await tasks.task_execute_once("request-123", {"trigger": {"type": "once"}}) == {
        "status": "replayed",
        "task_id": 31,
        "job_id": 41,
    }


async def test_execute_once_rejects_a_recurring_trigger_through_shared_guard(monkeypatch) -> None:
    from app.agent.toolset import tasks
    from app.services.task_contracts import TaskLaunchError

    async def launch(plan, *, request_id, job_type):
        assert job_type == "analyze"
        if plan.get("trigger", {}).get("type") != "once":
            raise TaskLaunchError("schedule_confirmation_required")
        return {"status": "queued"}

    _module(monkeypatch, "app.services.task_launch", launch_social_task=launch)
    result = await tasks.task_execute_once("request-123", {"trigger": {"type": "schedule", "cron": "0 9 * * *"}})
    assert result == {
        "error": "Recurring tasks require explicit schedule confirmation.",
        "code": "schedule_confirmation_required",
    }


async def test_execute_once_maps_plan_validation_to_a_bounded_error_code(monkeypatch) -> None:
    from app.agent.toolset import tasks
    from app.services.task_plans import TaskPlanError

    marker = "provider secret must not escape"

    async def launch(*_args, **_kwargs):
        raise TaskPlanError(marker)

    _module(monkeypatch, "app.services.task_launch", launch_social_task=launch)

    assert await tasks.task_execute_once("request-123", {"trigger": {"type": "once"}}) == {
        "error": "Invalid task plan (task_plan_invalid).",
        "code": "task_plan_invalid",
    }


async def test_execute_once_hides_known_and_unknown_external_error_text(monkeypatch) -> None:
    from app.agent.toolset import tasks
    from app.services.task_contracts import TaskLaunchError

    marker = "provider credential detail"

    async def known(*_args, **_kwargs):
        raise TaskLaunchError("request_id_conflict", cause=RuntimeError(marker))

    _module(monkeypatch, "app.services.task_launch", launch_social_task=known)
    known_result = await tasks.task_execute_once("request-123", {"trigger": {"type": "once"}})
    assert known_result == {
        "error": "request_id was already used for different task conditions",
        "code": "request_id_conflict",
    }
    assert marker not in str(known_result)

    async def unknown(*_args, **_kwargs):
        raise RuntimeError(marker)

    _module(monkeypatch, "app.services.task_launch", launch_social_task=unknown)
    unknown_result = await tasks.task_execute_once("request-123", {"trigger": {"type": "once"}})
    assert unknown_result == {
        "error": "The launch result is unconfirmed; inspect the task before retrying.",
        "code": "launch_unconfirmed",
    }
    assert marker not in str(unknown_result)


async def test_plan_previews_are_bounded_and_do_not_start_platform_work(monkeypatch) -> None:
    from app.agent.toolset import tasks

    async def entitlements(_payload):
        return {"allowed": True}

    async def vk_access(_plan):
        return {"reason": "legacy_source_access", "checked": False}

    monkeypatch.setattr("app.services.collection.entitlement_policy.preview_entitlements", entitlements)
    monkeypatch.setattr("app.services.social.vk_task_access.preview_vk_access", vk_access)

    result = await tasks.search_plan(
        goal="Find recent posts",
        platform="vk",
        source_scope="selected",
        source_ids=[10],
        queries=["repair", "renovation"],
    )
    assert result["status"] == "proposal"
    assert result["execution"] == "not_started"
    assert result["queries"] == ["repair", "renovation"]
    assert result["entitlements"] == {"allowed": True}
    assert result["vk_access"] == {"reason": "legacy_source_access", "checked": False}

    invalid = await tasks.search_plan(
        goal="Find recent posts",
        platform="vk",
        source_scope="selected",
        source_ids=[10],
        queries=[f"query {index}" for index in range(6)],
    )
    assert invalid["code"] == "queries_invalid"

    def parse(raw):
        return SimpleNamespace(normalized={"source_scope": "all", "source_ids": [], "platforms": ["vk"]})

    async def forbidden(*args, **kwargs):
        raise AssertionError("preview must not launch collection")

    def plan_block(parsed):
        return parsed.normalized

    _module(monkeypatch, "app.services.task_plans", parse_task_plan=parse, task_plan_block=plan_block)
    _module(
        monkeypatch,
        "app.models",
        AgentScenario=SimpleNamespace(objects=SimpleNamespace(get=forbidden)),
        Source=SimpleNamespace(objects=SimpleNamespace(select_related=forbidden)),
    )
    preview = await tasks.task_plan({"source_scope": "all"})
    assert preview["status"] == "validated_preview"
    assert preview["writes"] is False and preview["run_created"] is False


class _FirstQuery:
    def __init__(self, row):
        self.row = row

    async def first(self):
        return self.row


class _PlatformRows:
    def __init__(self, row):
        self.row = row

    def filter(self, **kwargs):
        assert kwargs == {"platform_type": "vk"}
        return _FirstQuery(self.row)


class _SearchClient:
    def __init__(self, *, cities=(), error=None):
        self.cities = list(cities)
        self.error = error
        self.search_calls = []

    async def get_cities(self, query, **kwargs):
        return self.cities

    async def search_groups(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        if self.error:
            raise self.error
        return []

    async def search_users(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        if self.error:
            raise self.error
        return []


def _install_search(monkeypatch, client):
    from app.agent.toolset import sources
    from app.core import tenant_context
    from app.models import Platform
    from app.services.social import factory, owner

    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 9)
    monkeypatch.setattr(Platform, "objects", _PlatformRows(SimpleNamespace(id=3, params={})), raising=False)

    async def source_owner(source):
        return 9

    monkeypatch.setattr(owner, "resolve_source_owner", source_owner)
    monkeypatch.setattr(factory, "get_social_client", lambda platform: client)
    return sources


async def test_ambiguous_city_stops_before_vk_search(monkeypatch) -> None:
    client = _SearchClient(
        cities=[
            {"id": 1, "title": "Springfield", "region": "North"},
            {"id": 2, "title": "Springfield", "region": "South"},
        ]
    )
    sources = _install_search(monkeypatch, client)

    result = await sources.sources_search("vk", "home repair", city="Springfield")

    assert result["code"] == "city_ambiguous"
    assert [candidate["id"] for candidate in result["candidates"]] == [1, 2]
    assert client.search_calls == []


async def test_unknown_city_does_not_expand_to_unfiltered_search(monkeypatch) -> None:
    client = _SearchClient(cities=[])
    sources = _install_search(monkeypatch, client)

    result = await sources.sources_search("vk", "home repair", city="No Such Place")

    assert result["code"] == "city_not_found"
    assert client.search_calls == []


async def test_unrelated_vk_runtime_error_is_not_misreported_as_1051(monkeypatch) -> None:
    client = _SearchClient(error=RuntimeError("Ошибка API (999): unrelated failure"))
    sources = _install_search(monkeypatch, client)

    result = await sources.sources_search("vk", "home repair")

    assert result["code"] == "vk_api_error:999"
    assert result["code"] != "api_1051"
    assert "error_msg" not in result


async def test_source_add_rejects_non_member_token_owner_and_raw_params(monkeypatch) -> None:
    from app.agent.toolset import sources
    from app.core import tenant_context
    from app.models import Platform
    from app.models.managers import tenant_manager

    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 9)
    monkeypatch.setattr(Platform, "objects", _PlatformRows(SimpleNamespace(id=3)), raising=False)

    async def memberships(tenant_id):
        assert tenant_id == 9
        return [SimpleNamespace(user_id=7)]

    monkeypatch.setattr(tenant_manager.tenant_users, "web_memberships_for_tenant", memberships)

    result = await sources.source_add("vk", "group", "-77", mode="user", token_owner=8)
    assert result["code"] == "token_owner_forbidden"
    parameters = inspect.signature(sources.source_add).parameters
    assert not {"params", "tenant_id", "owner_task_id"}.intersection(parameters)
    with pytest.raises(TypeError):
        await sources.source_add("vk", "group", "-77", params={"owner_task_id": 999, "tenant_id": 1})


async def test_source_add_persists_only_allowed_platform_params(monkeypatch) -> None:
    from app.agent.toolset import sources
    from app.core import tenant_context
    from app.models import Platform
    from app.models.managers import source_manager, tenant_manager

    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 9)
    monkeypatch.setattr(Platform, "objects", _PlatformRows(SimpleNamespace(id=3)), raising=False)

    async def memberships(tenant_id):
        return [SimpleNamespace(user_id=7)]

    class Manager:
        created = []

        async def get_source_by_platform(self, platform_id, external_id):
            return None

        async def create_source(self, **values):
            self.created.append(values)
            return SimpleNamespace(
                id=12,
                name=values["name"],
                source_type=values["source_type"],
                external_id=values["external_id"],
            )

    monkeypatch.setattr(tenant_manager.tenant_users, "web_memberships_for_tenant", memberships)
    monkeypatch.setattr(source_manager, "SourceManager", Manager)

    result = await sources.source_add(
        "vk", "search", "home repair", query="home repair", owner_ids=[-77], mode="user", token_owner=7
    )

    assert result["status"] == "created"
    params = Manager.created[-1]["params"]
    assert params == {"mode": "user", "token_owner": 7, "query": "home repair", "owner_ids": [-77]}
    assert set(params).issubset({"mode", "token_owner", "query", "owner_ids"})


def test_scenario_warning_names_missing_task_instruction_and_target() -> None:
    from app.agent.toolset.scenarios import _prompt_warnings

    draft = SimpleNamespace(
        base_prompt="Review posts in {text}",
        summary_prompt="",
        media_overrides={},
        analysis_types=["brand_mentions"],
    )

    warnings = _prompt_warnings(draft)

    assert any("payload_instruction" in warning for warning in warnings)
    assert any("бренд" in warning for warning in warnings)


def test_scenario_with_payload_instruction_has_no_missing_target_warning() -> None:
    from app.agent.toolset.scenarios import _prompt_warnings

    draft = SimpleNamespace(
        base_prompt="Use {payload_instruction} with {text}",
        summary_prompt="",
        media_overrides={},
        analysis_types=["sentiment"],
    )

    assert _prompt_warnings(draft) == []
