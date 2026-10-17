"""Taskless analyze must use the source's owning task scenario.

A staging-triggered analyze job has no `agent_task_id`: content admission wakes
an `analyze` run for a single source. That run used to fall straight to the
tenant default scenario, so a user whose task pinned a personal model on the
source watched the default scenario's model pay for the analysis. The source's
owner task owns the analysis — its scenario (and model) must win.

Covers:
- `AgentTaskManager.get_source_scenario` picks the single pinned scenario;
- falls back to None (tenant default) when no task or several tasks disagree;
- `handle_analyze` without a task passes the owning task's scenario to the
  analyzer instead of the workspace default scenario.
"""

from __future__ import annotations

import uuid

import pytest

from app.jobs.handlers import handle_analyze
from app.models import (
    AgentScenario,
    AgentTask,
    CollectedItem,
    Platform,
    Source,
)
from app.models.managers.agent_task_manager import AgentTaskManager
from app.types import SourceType


def _name(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=_name("p"),
        platform_type="vk",
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def source(platform):
    s = await Source.objects.create(
        platform_id=platform.id,
        name=_name("src"),
        source_type=SourceType.USER,
        external_id=_name("ext"),
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


async def _scenario(name: str, *, default: bool = False, model_id: int | None = None) -> AgentScenario:
    return await AgentScenario.objects.create(
        name=name, is_active=True, is_default=default, text_llm_model_id=model_id
    )


async def _task(scenario_id: int, source_id: int) -> AgentTask:
    t = await AgentTask.objects.create(
        name=_name("task"),
        cron_expr="0 * * * *",
        job_type="analyze",
        agent_scenario_id=scenario_id,
        is_active=True,
    )
    await AgentTaskManager().set_sources(t.id, [source_id])
    return t


async def _staged_row(source_id: int, i: int) -> CollectedItem:
    return await CollectedItem.objects.create(
        source_id=source_id,
        external_id=f"ext-{i}",
        content_hash=f"hash-{source_id}-{i}",
        platform="vk",
        text=f"sample text {i}",
        media_type="text",
        metrics={},
        author={},
        attachments=[],
        tenant_id=1,
    )


class TestGetSourceScenario:
    async def test_returns_scenario_of_single_owning_task(self, source):
        pinned = await _scenario("pinned", model_id=8)
        t = await _task(pinned.id, source.id)
        try:
            result = await AgentTaskManager().get_source_scenario(source.id)
            assert result is not None
            assert result.id == pinned.id
            assert result.text_llm_model_id == 8
        finally:
            await AgentTask.objects.delete_by_id(t.id)
            await AgentScenario.objects.delete_by_id(pinned.id)

    async def test_none_when_source_has_no_task(self, source):
        result = await AgentTaskManager().get_source_scenario(source.id)
        assert result is None

    async def test_none_when_owning_tasks_disagree(self, source):
        a = await _scenario("scen-a", model_id=8)
        b = await _scenario("scen-b", model_id=9)
        ta = await _task(a.id, source.id)
        tb = await _task(b.id, source.id)
        try:
            result = await AgentTaskManager().get_source_scenario(source.id)
            assert result is None
        finally:
            await AgentTask.objects.delete_by_id(ta.id)
            await AgentTask.objects.delete_by_id(tb.id)
            await AgentScenario.objects.delete_by_id(a.id)
            await AgentScenario.objects.delete_by_id(b.id)


class TestHandleAnalyzeScenario:
    async def test_taskless_analyze_uses_owning_task_scenario(self, source, monkeypatch):
        default_sc = await _scenario("default-scen", default=True, model_id=9)
        pinned = await _scenario("pinned", model_id=8)
        t = await _task(pinned.id, source.id)
        rows = [await _staged_row(source.id, i) for i in range(2)]

        captured: dict = {}

        async def _analyze_content(*args, **kwargs):
            scenario = kwargs.get("agent_scenario")
            captured["scenario_id"] = scenario.id if scenario else None
            src = next((a for a in args[2:] if a is not None and getattr(a, "id", None) is not None), None)
            captured["source_id"] = getattr(src, "id", None)
            return []

        monkeypatch.setattr(
            "app.services.ai.analyzer.AIAnalyzer",
            type("StubbedAnalyzer", (), {"__init__": lambda self: None, "analyze_content": _analyze_content}),
        )
        monkeypatch.setattr("app.services.ai.dedup.analysed_hashes", lambda fresh: set())

        try:
            stats = await handle_analyze({"source_ids": [source.id]})
            assert stats["sources"] == 1
        finally:
            for row in rows:
                await CollectedItem.objects.delete_by_id(row.id)
            await AgentTask.objects.delete_by_id(t.id)
            await AgentScenario.objects.delete_by_id(pinned.id)
            await AgentScenario.objects.delete_by_id(default_sc.id)

        assert captured.get("source_id") == source.id
        # The owning task's scenario (model 8), not the workspace default (model 9).
        assert captured.get("scenario_id") == pinned.id

    async def test_taskless_analyze_falls_back_when_no_owning_task(self, source, monkeypatch):
        rows = [await _staged_row(source.id, i) for i in range(2)]

        captured: dict = {}

        async def _analyze_content(*args, **kwargs):
            scenario = kwargs.get("agent_scenario")
            captured["scenario_id"] = scenario.id if scenario else None
            return []

        monkeypatch.setattr(
            "app.services.ai.analyzer.AIAnalyzer",
            type("StubbedAnalyzer", (), {"__init__": lambda self: None, "analyze_content": _analyze_content}),
        )
        monkeypatch.setattr("app.services.ai.dedup.analysed_hashes", lambda fresh: set())

        # The workspace default scenario (seeded or created earlier in this run)
        # is what the handler must fall back to without an owning task.
        from app.models.managers.agent_scenario_manager import AgentScenarioManager

        expected_default = await AgentScenarioManager().get_default_scenario(tenant_id=source.tenant_id)

        try:
            stats = await handle_analyze({"source_ids": [source.id]})
            assert stats["sources"] == 1
        finally:
            for row in rows:
                await CollectedItem.objects.delete_by_id(row.id)

        # No owning task → tenant default scenario (pre-change behaviour).
        assert expected_default is not None
        assert captured.get("scenario_id") == expected_default.id