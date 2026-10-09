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
        payload={},
        is_active=True,
    )
    try:
        await _replace_task_sources(task.id, [sources[0].id], task.tenant_id)

        called = []

        async def fake_collect(
            self, source, analyze=True, content_type="posts", force_reanalyze=False, run_id=None
        ):
            called.append(source.id)
            return _fake_result()

        monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.collect_from_source", fake_collect)

        stats = await handle_collect({"agent_task_id": task.id})
        assert called == [sources[0].id]
        assert stats["sources"] == 1
        assert stats["items"] == 3
    finally:
        await AgentTask.objects.delete_by_id(task.id)


async def test_the_collect_notification_separates_received_from_new(platform, sources, monkeypatch):
    """The 'добавлено или обновлено записей' line was never true.

    It printed `items` — the size of the platform's response — under a heading
    that claims rows were written. For a source that re-serves the same wall
    every hour it announced "32 added" on every run while writing nothing.
    """
    from app.jobs.dispatcher import _job_success_message

    job = type("J", (), {"job_type": "collect"})()
    job.result = {
        "sources": 1,
        "collected": 1,
        "empty": 0,
        "error": 0,
        "items": 32,
        "new_items": 0,
        "collected_sources": ["Кигель"],
    }
    msg = _job_success_message(job, job.result)
    assert "добавлено или обновлено" not in msg
    assert "Получено от источников: 32" in msg
    assert "Новых нет" in msg

    job.result["new_items"] = 5
    msg = _job_success_message(job, job.result)
    assert "новых, ранее не виденных: 5" in msg


async def test_the_collect_notification_admits_an_unmeasured_counter(platform, sources, monkeypatch):
    """A missing `new_items` means 'not measured', not '0 new'."""
    from app.jobs.dispatcher import _job_success_message

    job = type("J", (), {"job_type": "collect"})()
    job.result = {"sources": 1, "collected": 1, "items": 12, "collected_sources": ["Кигель"]}
    msg = _job_success_message(job, job.result)
    assert "не подсчитано" in msg
    assert "Новых нет" not in msg


async def test_handle_collect_records_a_per_source_breakdown(platform, sources, monkeypatch):
    """Each source's own yield must survive the run, not just the run total.

    `result["items"]` is a single number for the whole run, so with several
    sources there was no way to tell *which* one produced what — the question a
    source page has to answer. `per_source` keeps the breakdown, and a failing
    source is recorded too (as an error) rather than vanishing from the report.
    """
    from app.jobs.handlers import handle_collect

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        payload={},
        is_active=True,
    )
    try:
        from app.web.tasks import _replace_task_sources

        await _replace_task_sources(task.id, [sources[0].id, sources[1].id], task.tenant_id)

        async def fake_collect(
            self, source, analyze=True, content_type="posts", force_reanalyze=False, run_id=None
        ):
            if source.id == sources[1].id:
                raise RuntimeError("токен истёк")
            return {"content_count": 7, "analyzed": True, "analytics_count": 2}

        monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.collect_from_source", fake_collect)

        stats = await handle_collect({"agent_task_id": task.id})

        by_id = {row["source_id"]: row for row in stats["per_source"]}
        assert set(by_id) == {sources[0].id, sources[1].id}
        assert by_id[sources[0].id]["items"] == 7
        assert by_id[sources[0].id]["outcome"] == "collected"
        # collect analyses inline, so the stored analysis count is reported here.
        assert by_id[sources[0].id]["analyzed"] == 2
        assert by_id[sources[1].id]["outcome"] == "error"
        # The run-level totals keep working — the breakdown is additive.
        assert stats["items"] == 7
        assert stats["error"] == 1
    finally:
        await AgentTask.objects.delete_by_id(task.id)


async def test_handle_collect_reports_an_empty_source_as_empty(platform, sources, monkeypatch):
    """A source that yielded nothing is still an outcome worth recording."""
    from app.jobs.handlers import handle_collect

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        payload={},
        is_active=True,
    )
    try:
        from app.web.tasks import _replace_task_sources

        await _replace_task_sources(task.id, [sources[0].id], task.tenant_id)

        async def fake_collect(
            self, source, analyze=True, content_type="posts", force_reanalyze=False, run_id=None
        ):
            return None

        monkeypatch.setattr("app.services.monitoring.collector.ContentCollector.collect_from_source", fake_collect)

        stats = await handle_collect({"agent_task_id": task.id})

        assert stats["empty"] == 1
        assert stats["per_source"][0]["outcome"] == "empty"
        assert stats["per_source"][0]["items"] == 0
    finally:
        await AgentTask.objects.delete_by_id(task.id)


async def test_handle_collect_excludes_users(platform, sources, monkeypatch):
    """A source matching an excluded username is skipped before collection."""
    from app.jobs.handlers import handle_collect

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        payload={"excluded_users": ["skipme"]},
        is_active=True,
    )
    try:
        from app.web.tasks import _replace_task_sources

        await _replace_task_sources(task.id, [s.id for s in sources], task.tenant_id)

        called = []

        async def fake_collect(
            self, source, analyze=True, content_type="posts", force_reanalyze=False, run_id=None
        ):
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
    """Task payload monitored_users is passed through to the collector.

    Also pins the two arguments this branch used to drop: the run id (without
    which its raw copy could never be retired) and the `analyze_inline`
    decision (it hardcoded `analyze=True`, so the flag did nothing here).
    """
    from app.jobs.handlers import handle_collect

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        payload={"monitored_users": ["someone", "else"]},
        is_active=True,
    )
    try:
        from app.web.tasks import _replace_task_sources

        await _replace_task_sources(task.id, [sources[0].id], task.tenant_id)

        seen = {}

        async def fake_monitored(self, source, analyze=True, monitored_users=None, force_reanalyze=False, run_id=None):
            seen["source"] = source.id
            seen["users"] = monitored_users
            seen["analyze"] = analyze
            seen["run_id"] = run_id
            return {"total_items": 5, "total_users": 2, "successful": 2, "failed": 0}

        monkeypatch.setattr(
            "app.services.monitoring.collector.ContentCollector.collect_monitored_users", fake_monitored
        )

        stats = await handle_collect({"agent_task_id": task.id, "job_id": 4242})
        assert seen["source"] == sources[0].id
        assert seen["users"] == ["someone", "else"]
        assert stats["items"] == 5
        # Parking raw content is the default for a collect job, so `analyze=False`...
        assert seen["analyze"] is False
        # ...and the run id travels with it, so the raw rows can be retired later.
        assert seen["run_id"] == 4242
    finally:
        await AgentTask.objects.delete_by_id(task.id)


async def test_monitored_users_honours_analyze_inline_false(platform, sources, monkeypatch):
    """`analyze_inline: false` must reach the monitored-users branch too.

    This branch hardcoded `analyze=True`, so a task that asked to defer its
    analysis got it anyway — and paid for it.
    """
    from app.jobs.handlers import handle_collect

    task = await AgentTask.objects.create(
        name=f"t_{uuid.uuid4().hex[:6]}",
        job_type="collect",
        cron_expr="@once",
        payload={"monitored_users": ["someone"], "analyze_inline": False},
        is_active=True,
    )
    try:
        from app.web.tasks import _replace_task_sources

        await _replace_task_sources(task.id, [sources[0].id], task.tenant_id)

        seen = {}

        async def fake_monitored(self, source, analyze=True, monitored_users=None, force_reanalyze=False, run_id=None):
            seen["analyze"] = analyze
            return {"total_items": 5, "total_users": 1, "successful": 1, "failed": 0}

        monkeypatch.setattr(
            "app.services.monitoring.collector.ContentCollector.collect_monitored_users", fake_monitored
        )

        await handle_collect({"agent_task_id": task.id, "job_id": 4242})
        assert seen["analyze"] is False
    finally:
        await AgentTask.objects.delete_by_id(task.id)
