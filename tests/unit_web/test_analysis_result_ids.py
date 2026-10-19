"""Fresh analyzer row IDs must survive trigger filtering in the job result."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
_isolation_spec = spec_from_file_location("_result_ids_isolation", ROOT / "tests/source_import_isolation.py")
_isolation = module_from_spec(_isolation_spec)
_isolation_spec.loader.exec_module(_isolation)
load_isolated_source = _isolation.load_isolated_source


def load_handlers(fresh_rows):
    source = SimpleNamespace(id=23, name="source", tenant_id=1, external_id="source")
    scenario = SimpleNamespace(id=5, is_active=True)
    staged = [SimpleNamespace(as_agent_item=lambda: {"text": "content"}, content_hash="h1")]

    class Analyzer:
        def __init__(self):
            self.reported_errors = 0
            self.completed_hashes = set()
            self.current_fingerprint = "fingerprint"

        async def analyze_content(self, *_args, **_kwargs):
            return fresh_rows

    class Query:
        def order_by(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def __await__(self):
            return iter([])

    class Analytics:
        created_at = object()
        objects = SimpleNamespace(filter=Mock(return_value=Query()))

    class Scenario:
        objects = SimpleNamespace(get=AsyncMock(return_value=scenario))

    class Collected:
        objects = SimpleNamespace(
            count_for_source=AsyncMock(return_value=1),
            for_source=AsyncMock(return_value=staged),
        )

    progress = SimpleNamespace(
        begin_phase=AsyncMock(),
        step=AsyncMock(),
        update_current=AsyncMock(),
    )
    trigger = SimpleNamespace(should_analyze=AsyncMock(return_value=[]), should_act=AsyncMock())
    imports = {
        "app.services.social.credentials": SimpleNamespace(AuthorizationRequired=Exception),
        "app.models": SimpleNamespace(
            AgentScenario=Scenario,
            AIAnalytics=Analytics,
            BotAction=SimpleNamespace(objects=SimpleNamespace()),
            CollectedItem=Collected,
        ),
        "app.models.managers.agent_task_manager": SimpleNamespace(AgentTaskManager=object),
        "app.services.ai.trigger_evaluator": SimpleNamespace(trigger_evaluator=trigger),
        "app.services.social.guards": SimpleNamespace(
            extract_target_user=Mock(), guards_checker=SimpleNamespace(check=AsyncMock())
        ),
        "app.types": SimpleNamespace(BotActionStatus=SimpleNamespace(PENDING="pending")),
        "app.jobs": SimpleNamespace(progress=progress),
        "app.services.ai.analyzer": SimpleNamespace(AIAnalyzer=Analyzer),
        "app.services.ai.dedup": SimpleNamespace(analysed_hashes=Mock(return_value=set())),
    }
    module = load_isolated_source("_analysis_result_ids_handlers", ROOT / "app/jobs/handlers.py", imports)
    module._load_task = AsyncMock(return_value=None)
    module._resolve_sources = AsyncMock(return_value=[source])
    module._resolve_content_window = Mock(return_value={})
    module._retire_staged = AsyncMock(return_value=0)
    module._count_failed_staged = AsyncMock(return_value=0)
    module._llm_failure_code = AsyncMock(return_value=None)
    return module, trigger, Analytics


@pytest.mark.asyncio
async def test_fresh_persisted_ids_are_recorded_before_trigger_filtering():
    module, trigger, _ = load_handlers(
        [
            SimpleNamespace(id=101, summary_data={"summary": "a"}, response_payload={}),
            SimpleNamespace(id=102, summary_data={"summary": "b"}, response_payload={}),
            SimpleNamespace(id=101, summary_data={"summary": "duplicate"}, response_payload={}),
        ]
    )

    result = await module.handle_analyze({"source_ids": [23], "scenario_id": 5})

    assert result["per_source"][0]["analytics_ids"] == [101, 102]
    trigger.should_analyze.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_fresh_result_does_not_report_historic_analytics_ids():
    module, _, analytics = load_handlers([])

    result = await module.handle_analyze({"source_ids": [23], "scenario_id": 5})

    assert result["per_source"][0]["analytics_ids"] == []
    analytics.objects.filter.assert_not_called()


def test_ids_are_positive_unique_bounded_integers():
    module, _, _ = load_handlers([])
    rows = [SimpleNamespace(id=value) for value in [1, True, 0, -2, "3", 1]]

    assert module._fresh_analytics_ids(rows) == [1]
