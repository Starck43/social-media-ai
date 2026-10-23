"""Per-lane evidence projection and its rendering in the run panel.

`_lane_run_rows` is the only bridge between the persisted `collection_lane_runs`
row and the `summary.lane_runs` template context. A run that hit a cap must
show the applied number and the incomplete-result warning; a legacy row without
a stored cap keeps the generic warning instead of inventing "None".
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models import CollectionLaneRun
from app.web import tasks

ROOT = Path(__file__).resolve().parents[2]


class _LaneQuery:
    """The `filter(...).order_by(...)` chain, awaited for its rows."""

    def __init__(self, rows):
        self.rows = rows
        self.kwargs: dict = {}
        self.ordering: tuple = ()

    def filter(self, **kwargs):
        self.kwargs = kwargs
        return self

    def order_by(self, *ordering):
        self.ordering = ordering
        return self

    def __await__(self):
        async def _resolve():
            return self.rows

        return _resolve().__await__()


def _lane(**overrides):
    row = {
        "lane_id": "wall:vk:7",
        "lane_type": "wall_posts",
        "query": None,
        "fetched": 10,
        "limit_count": None,
        "selected": 8,
        "needs_review": 1,
        "rejected": 1,
        "limit_reached": False,
        "error_code": None,
        "unsupported_reason": None,
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def _install_rows(monkeypatch, rows):
    query = _LaneQuery(rows)
    monkeypatch.setattr(
        CollectionLaneRun,
        "objects",
        SimpleNamespace(filter=lambda **kwargs: query.filter(**kwargs)),
    )
    return query


@pytest.mark.asyncio
async def test_the_projection_preserves_the_persisted_limit_evidence(monkeypatch):
    """`limit_count` and `limit_reached` reach the template context unchanged."""
    reached = _lane(fetched=120, limit_count=120, limit_reached=True)
    legacy = _lane(lane_id="wall:vk:8", fetched=40, limit_reached=True)
    del legacy.limit_count
    query = _install_rows(monkeypatch, [reached, legacy])

    rows = await tasks._lane_run_rows(SimpleNamespace(id=31))

    assert query.kwargs == {"job_id": 31}
    assert len(query.ordering) == 1 and query.ordering[0] is CollectionLaneRun.id
    assert rows[0]["limit_count"] == 120
    assert rows[0]["limit_reached"] is True
    assert rows[0]["fetched"] == 120
    assert rows[0]["lane_type"] == "wall_posts"
    assert rows[1]["limit_count"] is None, "a legacy row reads as an absent cap, not zero"
    assert rows[1]["limit_reached"] is True


@pytest.mark.asyncio
async def test_a_reached_limit_renders_the_applied_cap_and_the_incomplete_warning(monkeypatch):
    """The real partial shows the applied N; a legacy cap stays generic."""
    _install_rows(
        monkeypatch,
        [
            _lane(fetched=120, limit_count=120, limit_reached=True),
            _lane(lane_id="wall:vk:8", fetched=40, limit_reached=True),
            _lane(lane_id="source:7", lane_type="source_stage", fetched=2, limit_count=2, limit_reached=True),
            _lane(lane_id="wall:vk:9", fetched=4, limit_count=120, limit_reached=False),
        ],
    )
    projected = await tasks._lane_run_rows(SimpleNamespace(id=31))

    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["human_dt"] = lambda value, **_kwargs: "сегодня"
    output = env.get_template("web/task_run_progress.html").render(
        task=SimpleNamespace(id=21, job_type="collect", last_run_at=None),
        job=SimpleNamespace(id=31, status="done", created_at=datetime(2026, 10, 20, tzinfo=timezone.utc)),
        summary={"status": "done", "label": "Завершено", "lane_runs": projected},
        sources=[],
        can_view_sources=False,
        run_progress=None,
        running_job_id=None,
        can_retry_analysis=False,
        csrf="",
        is_superuser=False,
        filter_tenant_id=None,
        _tz="UTC",
    )

    assert output.count("Достигнут лимит 120 постов; выборка может быть неполной") == 1
    assert output.count("Достигнут лимит 2 постов; выборка может быть неполной") == 1
    assert output.count("Достигнут лимит; выборка может быть неполной") == 1
    assert "Достигнут лимит 40" not in output
    assert "Достигнут лимит None" not in output
    assert "получено 120" in output
    assert "получено 40" in output
    assert "получено 2 постов" in output
