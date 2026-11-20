"""Autonomous contract tests for bounded effective agent-task ranges.

These tests stay at the manager/query boundary. They do not require a database,
provider, scheduler, or external service and intentionally exercise the same
security predicates used by ``upcoming_effective``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy.dialects import sqlite

from app.core import tenant_context
from app.models.managers.agent_task_manager import AgentTaskManager

__source_only__ = True


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.orderings = ()
        self.limit_value = None

    def order_by(self, *clauses):
        self.orderings = clauses
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def __await__(self):
        async def resolve():
            return list(self.rows)

        return resolve().__await__()


def _compiled(criteria):
    compiled = [criterion.compile(dialect=sqlite.dialect()) for criterion in criteria]
    return "\n".join(str(item) for item in compiled), [
        value for item in compiled for value in item.params.values()
    ]


@pytest.mark.asyncio
async def test_range_uses_half_open_utc_bounds_and_marks_truncation(monkeypatch):
    manager = AgentTaskManager()
    query = _Query([NS(id=1), NS(id=2), NS(id=3)])
    captured = {}

    def fake_filter(*criteria, **_kwargs):
        captured["criteria"] = criteria
        captured["query"] = query
        return query

    monkeypatch.setattr(manager, "filter", fake_filter)

    result = await manager.upcoming_effective_range(
        start=datetime(2026, 11, 1, 0, 0, tzinfo=timezone(timedelta(hours=3))),
        end=datetime(2026, 12, 1, 0, 0, tzinfo=timezone(timedelta(hours=3))),
        include_source_based=False,
        limit=2,
    )

    sql, params = _compiled(captured["criteria"])
    expected_start = datetime(2026, 10, 31, 21, 0, tzinfo=timezone.utc)
    expected_end = datetime(2026, 11, 30, 21, 0, tzinfo=timezone.utc)

    assert result == {"items": [query.rows[0], query.rows[1]], "complete": False}
    assert query.limit_value == 3
    assert any(value == expected_start for value in params)
    assert any(value == expected_end for value in params)
    assert "next_run_at >=" in sql
    assert "next_run_at <" in sql
    assert str(query.orderings[0]).endswith("next_run_at ASC")
    assert str(query.orderings[1]).endswith("id ASC")


@pytest.mark.asyncio
async def test_range_reuses_source_eligibility_switch_without_source_subqueries(monkeypatch):
    manager = AgentTaskManager()
    captured = {}

    def fake_filter(*criteria, **_kwargs):
        captured["criteria"] = criteria
        return _Query([])

    monkeypatch.setattr(manager, "filter", fake_filter)
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = datetime(2026, 12, 1, tzinfo=timezone.utc)

    await manager.upcoming_effective_range(
        start=start,
        end=end,
        include_source_based=False,
        limit=10,
    )
    denied_sql, _ = _compiled(captured["criteria"])

    await manager.upcoming_effective_range(
        start=start,
        end=end,
        include_source_based=True,
        limit=10,
    )
    allowed_sql, _ = _compiled(captured["criteria"])

    assert "NOT IN" in denied_sql.upper()
    assert "agent_task_sources" not in denied_sql
    assert "agent_task_sources" in allowed_sql
    assert "is_active" in allowed_sql
    assert "agent_scenarios" in allowed_sql


def test_manager_query_retains_context_tenant_guard(monkeypatch):
    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 42)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: False)

    manager = AgentTaskManager()
    query = manager.get_queryset()
    sql = "\n".join(
        str(criterion.compile(dialect=sqlite.dialect())) for criterion in query._criterion
    )
    params = [
        value
        for criterion in query._criterion
        for value in criterion.compile(dialect=sqlite.dialect()).params.values()
    ]

    assert "tenant_id" in sql
    assert 42 in params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 11, 1), datetime(2026, 12, 1, tzinfo=timezone.utc)),
        (datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 12, 1)),
        (datetime(2026, 12, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)),
    ],
)
async def test_range_rejects_naive_or_empty_intervals(start, end):
    with pytest.raises(ValueError):
        await AgentTaskManager().upcoming_effective_range(
            start=start,
            end=end,
            include_source_based=False,
        )
