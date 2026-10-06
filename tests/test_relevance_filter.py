"""Relevance filtering — meaningless / low-confidence rows are skipped on save.

Phase 3 of the analytics-chains task: when the scenario's scope sets
`relevance_filter: true`, `_save_analysis` drops rows the model itself flagged
as noise (`is_meaningful == false`) or too uncertain (`confidence <
min_confidence`) BEFORE any write, so a filtered day leaves no row behind and
the batch stays unanalysed (retryable). The filter is off by default — legacy
scenarios must keep saving exactly as before.
"""

import uuid

import pytest

from app.models import AIAnalytics, AgentScenario, Platform, Source
from app.services.ai.analyzer import AIAnalyzer
from app.types import PlatformType, SourceType


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type=PlatformType.VK.db_value,
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def source(platform):
    s = await Source.objects.create(
        platform_id=platform.id,
        name="Filter Source",
        source_type=SourceType.CHANNEL,
        external_id=f"filter-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


def _scenario(scope: dict) -> AgentScenario:
    return AgentScenario(id=1, name="filter-scn", scope=scope)


def _results(is_meaningful: bool = True, confidence: float = 1.0) -> dict:
    return {"text_analysis": {"parsed": {"is_meaningful": is_meaningful, "confidence": confidence}}}


async def test_meaningless_row_is_skipped(source):
    analyzer = AIAnalyzer()
    result = await analyzer._save_analysis(
        analysis_results=_results(is_meaningful=False),
        unified_summary={},
        source=source,
        content_stats={},
        platform_name="vk",
        agent_scenario=_scenario({"relevance_filter": True, "min_confidence": 0.6}),
        topic_chain_id="chain_1",
        chain_label="Тема",
    )
    assert result is None
    assert analyzer.filtered_skipped == 1
    assert await AIAnalytics.objects.filter(topic_chain_id="chain_1") == []


async def test_low_confidence_row_is_skipped(source):
    analyzer = AIAnalyzer()
    result = await analyzer._save_analysis(
        analysis_results=_results(is_meaningful=True, confidence=0.3),
        unified_summary={},
        source=source,
        content_stats={},
        platform_name="vk",
        agent_scenario=_scenario({"relevance_filter": True, "min_confidence": 0.6}),
        topic_chain_id="chain_2",
        chain_label="Тема",
    )
    assert result is None
    assert analyzer.filtered_skipped == 1


async def test_confident_meaningful_row_passes_the_filter(source, monkeypatch):
    analyzer = AIAnalyzer()
    created = []

    class _EmptyQS:
        async def first(self):
            return None

    async def fake_create(*a, **k):
        created.append(k)
        return AIAnalytics(id=1, source_id=source.id)

    monkeypatch.setattr(AIAnalytics.objects, "filter", lambda *a, **k: _EmptyQS())
    monkeypatch.setattr(AIAnalytics.objects, "create", fake_create)
    async def _price_usage(*a, **k):
        return 0
    monkeypatch.setattr(analyzer, "_price_usage", _price_usage)
    monkeypatch.setattr(analyzer, "_build_trace_payload", lambda *a, **k: {})
    monkeypatch.setattr(analyzer, "_make_json_serializable", lambda *a, **k: {})

    result = await analyzer._save_analysis(
        analysis_results=_results(is_meaningful=True, confidence=0.9),
        unified_summary={},
        source=source,
        content_stats={},
        platform_name="vk",
        agent_scenario=_scenario({"relevance_filter": True, "min_confidence": 0.6}),
        topic_chain_id="chain_3",
        chain_label="Тема",
    )
    assert result is not None
    assert created, "a passing row must be written"
    assert created[0]["chain_label"] == "Тема"
    assert analyzer.filtered_skipped == 0


async def test_filter_off_by_default_saves(source, monkeypatch):
    """No `relevance_filter` in scope → legacy behavior, nothing skipped."""
    analyzer = AIAnalyzer()
    created = []

    class _EmptyQS:
        async def first(self):
            return None

    async def fake_create(*a, **k):
        created.append(k)
        return AIAnalytics(id=1, source_id=source.id)

    monkeypatch.setattr(AIAnalytics.objects, "filter", lambda *a, **k: _EmptyQS())
    monkeypatch.setattr(AIAnalytics.objects, "create", fake_create)
    async def _price_usage(*a, **k):
        return 0
    monkeypatch.setattr(analyzer, "_price_usage", _price_usage)
    monkeypatch.setattr(analyzer, "_build_trace_payload", lambda *a, **k: {})
    monkeypatch.setattr(analyzer, "_make_json_serializable", lambda *a, **k: {})

    result = await analyzer._save_analysis(
        analysis_results=_results(is_meaningful=False),
        unified_summary={},
        source=source,
        content_stats={},
        platform_name="vk",
        agent_scenario=_scenario({}),
        topic_chain_id="chain_4",
        chain_label="Тема",
    )
    assert result is not None
    assert created, "legacy scenarios must keep saving"
    assert analyzer.filtered_skipped == 0


async def test_missing_model_fields_do_not_drop_the_row(source, monkeypatch):
    """LLM omitted is_meaningful/confidence → default to meaningful, keep the row."""
    analyzer = AIAnalyzer()
    created = []

    class _EmptyQS:
        async def first(self):
            return None

    async def fake_create(*a, **k):
        created.append(k)
        return AIAnalytics(id=1, source_id=source.id)

    monkeypatch.setattr(AIAnalytics.objects, "filter", lambda *a, **k: _EmptyQS())
    monkeypatch.setattr(AIAnalytics.objects, "create", fake_create)
    async def _price_usage(*a, **k):
        return 0
    monkeypatch.setattr(analyzer, "_price_usage", _price_usage)
    monkeypatch.setattr(analyzer, "_build_trace_payload", lambda *a, **k: {})
    monkeypatch.setattr(analyzer, "_make_json_serializable", lambda *a, **k: {})

    result = await analyzer._save_analysis(
        analysis_results={"text_analysis": {"parsed": {}}},
        unified_summary={},
        source=source,
        content_stats={},
        platform_name="vk",
        agent_scenario=_scenario({"relevance_filter": True, "min_confidence": 0.6}),
        topic_chain_id="chain_5",
        chain_label="Тема",
    )
    assert result is not None
    assert created, "absent fields must not be treated as noise"
