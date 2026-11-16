"""Task-result disclosure follows workspace rights and excludes raw diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.permissions import PermissionDeniedError, permission_scope
from app.core.tenant_context import tenant_scope
from app.services.task_results import task_result
from app.types import ActionType

__source_only__ = True


class Query:
    def __init__(self, *, first=None, rows=()):
        self.value = first
        self.rows = list(rows)

    def filter(self, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def first(self):
        return self.value

    def __await__(self):
        async def rows():
            return self.rows

        return rows().__await__()


def _models(monkeypatch):
    import app.models

    task_get = AsyncMock(
        return_value=SimpleNamespace(
            id=4,
            launch_job_id=9,
            last_status="ok",
            name="Research",
            job_type="analyze",
            is_active=False,
            cron_expr="@once",
            payload={},
        )
    )
    job = SimpleNamespace(
        id=9,
        status="done",
        attempts=2,
        result={
            "items": 2,
            "analyzed": 1,
            "progress": {
                "attempt": 2,
                "phase": "collection",
                "total": 2,
                "completed": 1,
                "items": [{"name": "Public VK group", "source_id": 37, "state": "running"}],
            },
            "prompt": "PRIVATE_PROMPT_SENTINEL",
            "per_source": [{"source_id": 3, "analytics_ids": [10]}],
        },
        error="PRIVATE_PROVIDER_SENTINEL",
    )
    analyses = SimpleNamespace(
        id=10, summary_data={"analysis_title": "Research", "summary": "A matching request", "provider_body": "secret"}
    )
    analysis_filter = lambda **kwargs: Query(rows=[analyses])
    field = SimpleNamespace(desc=lambda: "id descending")
    monkeypatch.setattr(app.models, "AgentTask", SimpleNamespace(objects=SimpleNamespace(get=task_get)))
    monkeypatch.setattr(
        app.models, "Job", SimpleNamespace(id=field, objects=SimpleNamespace(filter=lambda **kw: Query(first=job)))
    )
    monkeypatch.setattr(
        app.models, "AIAnalytics", SimpleNamespace(id=field, objects=SimpleNamespace(filter=analysis_filter))
    )
    return task_get


async def test_task_result_permission_denial_happens_before_any_query(monkeypatch):
    task_get = _models(monkeypatch)
    user = SimpleNamespace(id=7, is_active=True)
    with tenant_scope(2), permission_scope(user, member_grants=frozenset()):
        with pytest.raises(PermissionDeniedError):
            await task_result(4)
    task_get.assert_not_awaited()


async def test_task_view_without_analytics_rights_gets_status_and_counts_only(monkeypatch):
    _models(monkeypatch)
    user = SimpleNamespace(id=7, is_active=True)
    with tenant_scope(2), permission_scope(user, member_grants=frozenset({("agenttask", ActionType.VIEW)})):
        result = await task_result(4)
    assert result["job_status"] == "done"
    assert result["counts"] == {"items": 2, "analyzed": 1}
    assert result["progress"] == {"phase": "collection", "total": 2, "completed": 1, "updated_at": None}
    assert result["analyses"] == []
    assert "PRIVATE" not in str(result)


async def test_permitted_results_include_summary_links_but_no_raw_diagnostics(monkeypatch):
    _models(monkeypatch)
    user = SimpleNamespace(id=7, is_active=True)
    grants = frozenset((name, ActionType.VIEW) for name in ("agenttask", "source", "aianalytics"))
    with tenant_scope(2), permission_scope(user, member_grants=grants):
        result = await task_result(4, limit=1)
    assert result["analyses"] == [
        {"id": 10, "title": "Research", "summary": "A matching request", "url": "/app/analytics/10"}
    ]
    assert result["progress"]["items"] == [{"name": "Public VK group", "source_id": 37, "state": "running"}]
    assert "PRIVATE" not in str(result)
    assert "provider_body" not in str(result)


async def test_result_limit_is_checked_before_queries(monkeypatch):
    task_get = _models(monkeypatch)
    user = SimpleNamespace(id=7, is_active=True)
    with tenant_scope(2), permission_scope(user, member_grants=frozenset({("agenttask", ActionType.VIEW)})):
        with pytest.raises(ValueError, match="limit"):
            await task_result(4, limit=11)
    task_get.assert_not_awaited()
