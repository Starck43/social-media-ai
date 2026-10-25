"""Source-only contracts for task-plan editor defaults and activation gates."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app/web/tasks.py"


def _isolated_function(name: str, namespace: dict):
    tree = ast.parse(SOURCE.read_text())
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, f"source_only_{name}", "exec"), namespace)
    return namespace[name]


def test_edit_payload_preserves_fixed_legacy_dates_and_plan_window():
    collection_edit = {"start_date": "2026-01-01", "end_date": "2026-01-31"}
    edit_payload = _isolated_function(
        "_edit_payload",
        {"_normalize_web_job_type": lambda value: value, "_collection_edit": lambda payload: collection_edit},
    )

    legacy = edit_payload(
        NS(id=2, name="legacy", job_type="analyze", cron_expr="0 0 * * *", payload={"cli_dates": {"start_date": "2026-01-01"}},
           trigger_config={}, trigger_type=None, action_type=None, rate_limit_per_hour=None, cooldown_seconds=None),
        set(), [],
    )
    assert legacy["window_type"] == "fixed"

    planned = edit_payload(
        NS(id=3, name="plan", job_type="analyze", cron_expr="0 0 * * *", payload={"task_plan": {"window": {"type": "rolling", "days": 5}}},
           trigger_config={}, trigger_type=None, action_type=None, rate_limit_per_hour=None, cooldown_seconds=None),
        set(), [],
    )
    assert planned["window_type"] == "rolling"
    assert planned["rolling_days"] == 5


@pytest.mark.asyncio
async def test_discovery_query_plan_can_activate_without_sources_but_legacy_cannot():
    @contextmanager
    def tenant_scope(_tenant_id):
        yield

    source_query = NS(values=lambda _field: NS(rows=AsyncMock(return_value=[])))
    source_manager = NS(objects=NS(filter=AsyncMock(return_value=source_query)))
    scenario_manager = NS(objects=NS(get=AsyncMock(return_value=None)))
    fake_models = NS(AgentScenario=scenario_manager, Source=source_manager)
    fake_task_manager = NS(requires_sources=lambda _job_type: True)

    import sys
    from unittest.mock import patch

    gate = _isolated_function("_can_activate", {
        "Any": __import__("typing").Any,
        "AgentScenario": scenario_manager,
        "Source": source_manager,
    })
    with patch.dict(sys.modules, {
        "app.core.tenant_context": NS(tenant_scope=tenant_scope),
        "app.models.managers.agent_task_manager": NS(AgentTaskManager=fake_task_manager),
    }):
        query_plan = {"task_plan": {"source_scope": "discovery", "collection": {"discovery": {"queries": ["coffee"]}}}}
        assert await gate(9, "analyze", [], None, payload=query_plan) is None
        assert await gate(9, "analyze", [], None, payload={}) is not None
