"""Задача не подменяет сбор стеной пользователя: `tracked_users` — это фильтр.

Owner command:
`.venv/bin/python -m pytest tests/test_handle_collect_selection.py -q --noconftest -o addopts=`

Дизайн: `docs/design/task-selection.md`. Задача собирает только свои источники
(m2m `agent_task_sources`), а `payload["tracked_users"]` решает, чьи записи
получат анализ, — никто не читает ничьих личных стен.

Тест работает с настоящим источником `app/jobs/handlers.py`, но все зависимости
(задача, источники, коллектор, прогресс) подменены двойниками: БД и сеть не
трогаются. Страховка от реальных запросов — патч на пул/движок SQLAlchemy,
asyncpg и httpx: любой их вызов падает сразу, а не идёт в базу.
"""

import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.jobs import handlers as module
from app.services.collection.context import build_collection_context
from app.types import SourceType

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_PATH = "app.services.monitoring.collector.ContentCollector"


def _make_collector():
    """Коллектор, у которого обе ветки — счётчики вызовов.

    `collect_monitored_users` создаётся намеренно: если обработчик всё ещё
    читает `monitored_users`, двойник перехватит ветку и тест скажет, какая
    именно сработала, вместо невнятного `AttributeError`.
    """
    return SimpleNamespace(
        collect_from_source=AsyncMock(
            return_value={"content_count": 3, "new_items": 1, "staged": 3, "analytics_count": 0}
        ),
        collect_monitored_users=AsyncMock(
            return_value={"total_items": 99, "total_new_items": 99, "successful": 1, "failed": 0}
        ),
    )


@pytest.fixture
def env(monkeypatch):
    """Один источник и задача без привязки к БД."""
    collector = _make_collector()
    source = SimpleNamespace(
        id=11,
        name="wall",
        # A screen-name id: its numeric owner can only be resolved through the
        # platform. A name that is not numeric cannot be compared by digits, so
        # the read happens and each item is selected individually.
        external_id="wall",
        params={},
        tenant_id=1,
        # The fields the run's evidence step reads to classify the lane a
        # source reports under: a group wall reports the wall lane.
        source_type=SourceType.GROUP,
        platform=SimpleNamespace(platform_type="vk"),
    )
    task = SimpleNamespace(id=5, payload={}, tenant_id=1, agent_scenario=None, trigger_config=None)

    monkeypatch.setattr(module, "_load_task", AsyncMock(return_value=task))
    monkeypatch.setattr(module, "_resolve_sources", AsyncMock(return_value=[source]))
    # `ContentCollector()` внутри обработчика — тот же двойник.
    monkeypatch.setattr(COLLECTOR_PATH, lambda: collector)
    # The lane-evidence write goes through the ORM; it is doubled here so the
    # panic guards below stay meaningful for everything else the run touches.
    record_lane = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.collection.lane_runs.record_lane_run", record_lane)
    # Прогресс пишет в строку Job через БД — здесь это чистые заглушки.
    progress = SimpleNamespace(step=AsyncMock(), update_current=AsyncMock(), begin_phase=AsyncMock())
    monkeypatch.setattr("app.jobs.progress.step", progress.step)
    monkeypatch.setattr("app.jobs.progress.update_current", progress.update_current)
    monkeypatch.setattr("app.jobs.progress.begin_phase", progress.begin_phase)
    return SimpleNamespace(collector=collector, source=source, task=task, progress=progress, record_lane=record_lane)


@pytest.fixture(autouse=True)
def _no_external_calls():
    """Любой SQL/HTTP из-под обработчика — падение теста, а не поход в базу."""
    import asyncpg
    import httpx
    from sqlalchemy import engine as sa_engine
    from sqlalchemy import pool as sa_pool

    def panic(*_args, **_kwargs):
        raise AssertionError("handle_collect попытался обратиться к БД или сети")

    with patch.object(sa_engine.Engine, "connect", panic), patch.object(
        sa_pool.Pool, "connect", panic
    ), patch.object(asyncpg.Connection, "execute", panic), patch.object(httpx.Client, "send", panic):
        yield


def _collect(env):
    return asyncio.run(module.handle_collect({"agent_task_id": env.task.id}))


class TestTrackedUsersAreNotACollectionTarget:
    def test_tracked_users_never_replaces_collection_with_a_users_wall(self, env):
        """`{"tracked_users": ["vk:1"]}` — источник задачи собирается как обычно.

        Старое поведение: список пользователей подменял стену источника на
        стены перечисленных людей, поэтому из собственных источников задачи не
        анализировалось ничего. Фильтр по автору применяется позже, при
        анализе (`handle_analyze`), и сбора не касается.
        """
        env.source.external_id = "-555"
        env.task.payload = {"tracked_users": ["vk:777"]}

        stats = _collect(env)

        env.collector.collect_from_source.assert_awaited_once()
        assert env.collector.collect_monitored_users.await_count == 0
        assert stats["sources"] == 1
        assert stats["per_source"][0]["source_id"] == env.source.id

    def test_monitored_users_in_payload_is_not_read(self, env):
        """Старый ключ не имеет читателя: он не включает monitored-ветку.

        Никакого «legacy read path» у ключа нет (docs/design/task-selection.md).
        Обработчик идёт обычным путём сбора источника — не падает и не угадывает
        намерение.
        """
        env.task.payload = {"monitored_users": ["someone", "else"]}

        stats = _collect(env)

        env.collector.collect_from_source.assert_awaited_once()
        assert env.collector.collect_monitored_users.await_count == 0
        assert stats["items"] == 3

    def test_excluded_users_in_payload_is_not_read(self, env):
        """Удалённый ключ не имеет читателя: сбор идёт как обычно.

        «excluded_users» убран из алгоритмов/форм/CLI вместе с blacklist и
        whitelist; оставшийся в чужом payload ключ не влияет на сбор.
        """
        env.task.payload = {"tracked_users": ["vk:1"], "excluded_users": ["wall"]}

        stats = _collect(env)

        env.collector.collect_from_source.assert_awaited_once()
        assert env.collector.collect_monitored_users.await_count == 0
        assert stats["excluded"] == 0
        assert stats["sources"] == 1

    def test_collect_analysis_flag_and_run_id_are_unchanged(self, env):
        """Остальные аргументы сбора (analyze, run_id) рефакторинг не задевает."""
        env.source.external_id = "-555"
        env.task.payload = {"tracked_users": ["vk:777"]}

        asyncio.run(module.handle_collect({"agent_task_id": env.task.id, "job_id": 4242}))

        kwargs = env.collector.collect_from_source.await_args.kwargs
        assert kwargs["analyze"] is False, "collect по умолчанию паркует сырьё, а не анализирует"
        assert kwargs["run_id"] == 4242


class TestMonitoredUsersIsGone:
    def test_payload_key_has_no_reader_left_in_the_handler(self):
        """AST: в `handlers.py` не осталось литерала с `monitored`.

        Защищает от возврата `payload.get("monitored_users")` при правке
        обработчика: строка не должна появляться в источнике вовсе.
        """
        assert module.__file__.endswith("app/jobs/handlers.py")
        tree = ast.parse((ROOT / "app" / "jobs" / "handlers.py").read_text(encoding="utf-8"))
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "monitored" in node.value
        }
        assert literals == set(), f"в обработчике остался ключ monitored: {sorted(literals)}"


class TestLaneEvidenceClassifiesTheSource:
    """The per-source evidence step reads the source's own type and platform.

    One row per collected source is written beside the per-source counters;
    which lane it names is decided from the source, so the double carries the
    fields the handler reads (`source_type`, `platform`).
    """

    def test_a_wall_source_reports_the_wall_lane(self, env):
        """A group wall reports `wall_posts` under its platform and id."""
        stats = _collect(env)

        env.record_lane.assert_awaited_once()
        kwargs = env.record_lane.await_args.kwargs
        assert kwargs["lane_id"] == "wall:vk:11"
        assert kwargs["lane_type"] == "wall_posts"
        assert kwargs["query"] is None
        assert kwargs["tenant_id"] == 1
        assert kwargs["task_id"] == 5
        assert kwargs["fetched"] == 3, "the evidence row mirrors the per-source counter"
        assert stats["per_source"][0]["items"] == 3, "the counters stay beside the evidence"

    @pytest.mark.parametrize("source_type", [None, "album"], ids=["unreadable", "unmodelled"])
    def test_a_source_with_no_readable_type_does_not_fail_the_run(self, env, source_type):
        """Evidence classifies defensively: no lane, and the run is unaffected.

        The type is untrusted row data — `None` or a collector special the
        planner does not model means the source simply reports no lane, and
        the collection itself must not notice.
        """
        env.source.source_type = source_type

        stats = _collect(env)

        env.record_lane.assert_not_awaited()
        assert stats["sources"] == 1
        assert stats["items"] == 3
        assert stats["per_source"][0]["source_id"] == env.source.id
        assert stats["per_source"][0]["outcome"] == "collected"


class TestCollectionContextAndRunScope:
    """Which collection scope the handler hands to the collector.

    A task run — including a detached replay of one — carries its own parsed
    context and one deduplication set per invocation. A taskless CLI run hands
    the collector neither, so the standalone per-source legacy cap stays in
    charge; the handler still summarises the source from its local context.
    """

    def test_taskless_run_passes_no_task_context_to_the_collector(self, env, monkeypatch):
        """Taskless collection keeps the standalone source-specific legacy cap.

        The handler builds a context for its own summary, but the collector
        must receive `None`: a source-level cap is not a task intent, and
        cross-source deduplication belongs to container-level runs only.
        """
        monkeypatch.setattr(module, "_load_task", AsyncMock(return_value=None))

        stats = asyncio.run(module.handle_collect({}))

        kwargs = env.collector.collect_from_source.await_args.kwargs
        assert kwargs["collection_context"] is None
        assert kwargs["seen_items"] is None
        assert kwargs["task_id"] is None
        assert stats["sources"] == 1
        assert stats["per_source"][0]["outcome"] == "collected"

    def test_task_run_passes_its_context_and_a_run_scoped_seen_set(self, env):
        """A task run owns the intent and the one deduplication set of the run."""
        env.task.payload = {"collection": {"limit": 250}}

        stats = _collect(env)

        kwargs = env.collector.collect_from_source.await_args.kwargs
        context = kwargs["collection_context"]
        assert context is not None
        assert context.platform == "vk"
        assert context.source_id == env.source.id
        assert context.intent.limit == 250
        assert type(kwargs["seen_items"]) is set
        assert kwargs["seen_items"] == set(), "the set starts empty on every invocation"
        assert kwargs["task_id"] == env.task.id
        assert stats["per_source"][0]["collection"]["limit"] == 250

    def test_replay_run_passes_a_rebuilt_context_and_its_own_seen_set(self, env):
        """A detached replay is still a task-context run, without a task row."""
        payload = {
            "retry_of_job_id": 99,
            "_effective_snapshot": {
                "version": 1,
                "dry_run": False,
                "task_payload": {
                    "source_ids": [env.source.id],
                    "cli_dates": {"start_date": "2026-01-01", "end_date": "2026-02-01"},
                    "collection": {"limit": 300},
                },
            },
        }

        stats = asyncio.run(module.handle_collect(payload))

        kwargs = env.collector.collect_from_source.await_args.kwargs
        context = kwargs["collection_context"]
        assert context is not None, "the snapshot's task payload drives the replay intent"
        assert context.intent.limit == 300
        assert type(kwargs["seen_items"]) is set
        assert kwargs["seen_items"] == set()
        assert kwargs["task_id"] is None, "a replay carries no task row"
        assert stats["per_source"][0]["collection"]["limit"] == 300
        env.record_lane.assert_awaited_once()

    def test_taskless_run_summarises_the_source_from_its_local_context(self, env, monkeypatch):
        """A result without a `collection` key still gets the local summary.

        The collector double returns content but no context summary, so the
        per-source row must fall back to the context the handler built itself
        instead of dereferencing the `None` it passed to the collector.
        """
        monkeypatch.setattr(module, "_load_task", AsyncMock(return_value=None))
        env.collector.collect_from_source.return_value = {"content_count": 2, "new_items": 2, "staged": 2}

        stats = asyncio.run(module.handle_collect({}))

        kwargs = env.collector.collect_from_source.await_args.kwargs
        assert kwargs["collection_context"] is None
        per = stats["per_source"][0]
        assert per["items"] == 2
        expected = build_collection_context({}, platform="vk", source_id=env.source.id).summary()
        assert per["collection"] == expected
        assert per["collection"]["platform"] == "vk"
        assert per["collection"]["content_types"] == ["posts"]
