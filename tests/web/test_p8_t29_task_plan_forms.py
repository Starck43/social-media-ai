"""Task editor defaults and search activation; no database or platform calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.web import tasks

__source_only__ = True


def _task(payload):
    return SimpleNamespace(
        id=2,
        name="research",
        job_type="analyze",
        cron_expr="0 0 * * *",
        payload=payload,
        agent_scenario_id=None,
        trigger_config={},
        trigger_type=None,
        action_type=None,
        rate_limit_per_hour=None,
        cooldown_seconds=None,
        dry_run=False,
    )


def test_edit_payload_preserves_fixed_legacy_dates_and_plan_window(monkeypatch):
    monkeypatch.setattr(
        tasks, "_collection_edit", lambda payload: {"start_date": "2026-01-01", "end_date": "2026-01-31"}
    )
    legacy = tasks._edit_payload(_task({"cli_dates": {"start_date": "2026-01-01"}}), set(), [])
    assert legacy["window_type"] == "fixed"
    planned = tasks._edit_payload(_task({"task_plan": {"window": {"type": "rolling", "days": 5}}}), set(), [])
    assert planned["window_type"] == "rolling"
    assert planned["rolling_days"] == 5


@pytest.mark.parametrize(
    "collection",
    [
        {"queries": ["coffee"]},
        {"discovery": {"queries": ["coffee"], "communities": True}},
    ],
)
async def test_discovery_query_plan_can_activate_without_existing_sources(monkeypatch, collection):
    rows = AsyncMock(return_value=[])
    manager = SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(values=lambda *args: SimpleNamespace(rows=rows)))
    monkeypatch.setattr(tasks, "Source", SimpleNamespace(id=1, objects=manager))
    plan = {"task_plan": {"source_scope": "discovery", "collection": collection}}
    assert await tasks._can_activate(9, "analyze", [], None, payload=plan) is None
    rows.assert_not_awaited()
    assert await tasks._can_activate(9, "analyze", [], None, payload={}) is not None
    rows.assert_awaited_once()


def test_explicit_rolling_window_can_replace_fixed_collection_dates():
    plan = tasks._task_plan_from_form(
        "Research",
        ["vk"],
        "selected",
        [10],
        2,
        "analyze",
        "@once",
        {
            "collection": {"date_window": {"start": "2026-01-01", "end": "2026-01-02"}},
            "cli_dates": {"start_date": "2026-01-01", "end_date": "2026-01-02"},
        },
        "rolling",
        3,
    )
    assert plan["window"] == {"type": "rolling", "days": 3}
    assert "date_window" not in plan["collection"]
