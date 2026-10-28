"""Bounded no-live-DB contracts for source/scenario operator navigation."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment
from sqlalchemy.dialects import postgresql

from app.models.managers.agent_task_manager import AgentTaskManager

ROOT = Path(__file__).resolve().parents[2]


class _AwaitableQuery:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.criteria = None
        self.prefetched = ()
        self.ordering = ()

    def prefetch_related(self, *values):
        self.prefetched = values
        return self

    def order_by(self, *values):
        self.ordering = values
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def __await__(self):
        async def resolve():
            return self.rows

        return resolve().__await__()


def _query_manager():
    manager = AgentTaskManager.__new__(AgentTaskManager)
    query = _AwaitableQuery()

    def capture(*criteria, **kwargs):
        query.criteria = criteria
        query.kwargs = kwargs
        return query

    manager.filter = capture
    return manager, query


@pytest.mark.asyncio
async def test_source_task_query_is_tenant_and_job_scoped_with_all_active_semantics():
    manager, query = _query_manager()
    await manager.for_source(17, 9, source_is_active=True)

    sql = " ".join(str(clause.compile(dialect=postgresql.dialect())) for clause in query.criteria)
    assert "agent_tasks.tenant_id = %(tenant_id_1)s" in sql
    assert "agent_tasks.job_type IN" in sql
    assert "agent_task_sources.source_id = %(source_id_1)s" in sql
    assert "NOT (EXISTS" in sql  # unlinked tasks mean all active sources
    assert query.prefetched == ("agent_scenario", "sources")


@pytest.mark.asyncio
async def test_inactive_source_does_not_match_wildcard_tasks():
    manager, query = _query_manager()
    await manager.for_source(17, 9, source_is_active=False)

    sql = " ".join(str(clause.compile(dialect=postgresql.dialect())) for clause in query.criteria)
    assert "agent_tasks.tenant_id = %(tenant_id_1)s" in sql
    assert "agent_task_sources.source_id = %(source_id_1)s" in sql
    assert "NOT (EXISTS" not in sql


def _load_recent_collections():
    tree = ast.parse((ROOT / "app/web/sources.py").read_text())
    function = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_recent_collections"
    )
    function.body = [
        node
        for node in function.body
        if not (isinstance(node, ast.ImportFrom) and node.module == "app.models.scheduling.job")
    ]
    module = ast.Module(body=[function], type_ignores=[])
    scope = {
        "COLLECTION_HISTORY_LIMIT": 10,
        "human_datetime": lambda value: value,
        "_attach_raw_previews": _empty_attach,
    }
    exec(compile(ast.fix_missing_locations(module), "isolated recent collections", "exec"), scope)
    return scope["_recent_collections"]


async def _empty_attach(_rows):
    return None


@pytest.mark.asyncio
async def test_collection_history_ignores_standalone_analyze_jobs():
    rows = [
        SimpleNamespace(
            id=1,
            job_type="analyze",
            created_at="now",
            status="done",
            result={"per_source": [{"source_id": 17, "analyzed": 2}]},
        ),
        SimpleNamespace(
            id=2,
            job_type="analyze",
            created_at="earlier",
            status="done",
            result={
                "per_source": [
                    {
                        "source_id": 17,
                        "analyzed": 3,
                        "collection": {"items": 4, "new_items": 3, "outcome": "collected"},
                    }
                ]
            },
        ),
    ]
    query = _AwaitableQuery(rows)
    field = SimpleNamespace(desc=lambda: "created_at DESC")
    job_model = SimpleNamespace(id=1, created_at=field, objects=SimpleNamespace(filter=lambda **_kwargs: query))
    recent = _load_recent_collections()
    recent.__globals__["Job"] = job_model

    result = await recent(17)

    assert [entry["job_id"] for entry in result] == [2]
    assert result[0]["items"] == 4
    assert result[0]["new_items"] == 3


def test_source_and_scenario_pages_point_to_their_next_setup_step():
    source = (ROOT / "app/web/templates/web/source_detail.html").read_text()
    scenario = (ROOT / "app/web/templates/web/scenario_wizard.html").read_text()

    assert "/app/tasks?create=1&amp;source_id={{ source.id }}" in source
    assert "Добавить задачу" not in source  # one source-to-task action, no duplicate collect/analyze buttons
    assert "{% if usage is not none %}" in scenario
    assert "{% if can_create_tasks and draft.is_active %}" in scenario
    assert "/app/tasks?create=1&amp;scenario_id={{ scenario_id }}" in scenario
    assert "Активируйте сценарий, чтобы создать задачу по нему." in scenario


def test_inactive_scenario_does_not_offer_prefill_that_the_task_form_would_drop():
    source = (ROOT / "app/web/templates/web/scenario_wizard.html").read_text()
    start = source.index("    {% if usage is not none %}")
    end = source.index("    {% set ro =", start)
    usage_block = Environment().from_string(source[start:end])
    context = {
        "usage": {
            "total": 0,
            "active": 0,
            "paused": 0,
            "last_status": None,
            "next_run": "не запланирован",
            "tasks": [],
        },
        "scenario_id": 5,
        "can_create_tasks": True,
        "draft": SimpleNamespace(is_active=True),
    }

    active = usage_block.render(**context)
    context["draft"] = SimpleNamespace(is_active=False)
    inactive = usage_block.render(**context)

    assert "/app/tasks?create=1&amp;scenario_id=5" in active
    assert "/app/tasks?create=1" not in inactive
    assert "Активируйте сценарий, чтобы создать задачу по нему." in inactive
