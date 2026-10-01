"""Tests for the collect job handler: scenario-conditioned source selection.

Verifies that `handle_collect` correctly applies the task's m2m sources,
monitored_users override and excluded_users filter (docs/AGENT_TASKS.md).
"""

import uuid

import pytest

from app.models import AgentTask, Platform, Source
from app.types import SourceType


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type="vk",
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def sources(platform):
    items = []
    for name in ("alpha", "beta", "skipme"):
        s = await Source.objects.create(
            platform_id=platform.id,
            name=name,
            source_type=SourceType.CHANNEL.name,
            external_id=name,
            is_active=True,
        )
        items.append(s)
    yield items
    for s in items:
        await Source.objects.delete_by_id(s.id)


def _fake_result(content_count=3):
    return {"content_count": content_count, "analyzed": True, "analytics_count": 1}


async def test_handle_collect_uses_m2m_sources(platform, sources, monkeypatch):
    """Sources come from the task's m2m set, not every active source."""
    from app.jobs.handlers import handle_collect
    from app.web.tasks import _replace_task_sources

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        timezone="Europe/Moscow",
        payload={},
        is_active=True,
    )
    try:
        await _replace_task_sources(task.id, [sources[0].id], task.tenant_id)

        called = []

        async def fake_collect(self, source, analyze=True, content_type="posts", analyze_by=None):
            called.append(source.id)
            return _fake_result()

        monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.collect_from_source", fake_collect)

        stats = await handle_collect({"agent_task_id": task.id})
        assert called == [sources[0].id]
        assert stats["sources"] == 1
        assert stats["items"] == 3
    finally:
        await AgentTask.objects.delete_by_id(task.id)


async def test_handle_collect_excludes_users(platform, sources, monkeypatch):
    """A source matching an excluded username is skipped before collection."""
    from app.jobs.handlers import handle_collect

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        timezone="Europe/Moscow",
        payload={"excluded_users": ["skipme"]},
        is_active=True,
    )
    try:
        from app.web.tasks import _replace_task_sources

        await _replace_task_sources(task.id, [s.id for s in sources], task.tenant_id)

        called = []

        async def fake_collect(self, source, analyze=True, content_type="posts", analyze_by=None):
            called.append(source.id)
            return _fake_result()

        monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.collect_from_source", fake_collect)

        stats = await handle_collect({"agent_task_id": task.id})
        assert called == [sources[0].id, sources[1].id]
        assert sources[2].id not in called
        assert stats["excluded"] == 1
        assert stats["sources"] == 2
    finally:
        await AgentTask.objects.delete_by_id(task.id)


async def test_handle_collect_passes_monitored_users_override(platform, sources, monkeypatch):
    """Task payload monitored_users is passed through to the collector."""
    from app.jobs.handlers import handle_collect

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        timezone="Europe/Moscow",
        payload={"monitored_users": ["someone", "else"]},
        is_active=True,
    )
    try:
        from app.web.tasks import _replace_task_sources

        await _replace_task_sources(task.id, [sources[0].id], task.tenant_id)

        seen = {}

        async def fake_monitored(self, source, analyze=True, monitored_users=None):
            seen["source"] = source.id
            seen["users"] = monitored_users
            return {"total_items": 5, "total_users": 2, "successful": 2, "failed": 0}

        monkeypatch.setattr(
            "app.services.monitoring.collector.ContentCollector.collect_monitored_users", fake_monitored
        )

        stats = await handle_collect({"agent_task_id": task.id})
        assert seen["source"] == sources[0].id
        assert seen["users"] == ["someone", "else"]
        assert stats["items"] == 5
    finally:
        await AgentTask.objects.delete_by_id(task.id)
