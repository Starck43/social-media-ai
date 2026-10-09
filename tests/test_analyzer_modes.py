"""Tests for the analyzer (themes mode only - other modes were removed).

Verifies the analyzer only supports "themes" mode and that the
theme grouping works correctly. `base_analyze_content` is stubbed — no LLM, no DB writes.
"""

import uuid

import pytest

from app.models import Platform, Source
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
        name="Mode Test Source",
        source_type=SourceType.CHANNEL,
        external_id="mode-test",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


async def test_analyze_content_has_no_report_grouping_parameter(monkeypatch, source):
    """Write-path analysis always clusters by theme, not a reporting axis."""
    import inspect

    called = []

    async def fake_by_themes(self, content, source, **kwargs):
        called.append(kwargs)
        return []

    monkeypatch.setattr(AIAnalyzer, "_analyze_content_by_themes", fake_by_themes)
    assert "analyze_by" not in inspect.signature(AIAnalyzer.analyze_content).parameters
    assert await AIAnalyzer().analyze_content([{"text": "x"}], source) == []
    assert len(called) == 1


async def test_base_analyze_does_not_save_a_timed_out_llm_stub(monkeypatch, source):
    """A timed-out/errored LLM result must not be saved as a real analysis.

    On timeout the client returns `parsed={"analysis": "Timeout"}`. Saving that
    would mark the batch as analyzed (dedup) and block a retry, leaving a
    stats-only chain entry with no conclusion. So `base_analyze_content` must
    treat an error stub as "no result" and skip the save.
    """
    from app.services.ai.content_classifier import ContentClassifier

    saved = []

    async def fake_analyze_text(
        self, text_items, agent_scenario, content_stats, platform_name, source, trigger_config=None, task_payload=None, **kwargs
    ):
        return {
            "request": {"model": "m", "prompt": "p", "provider": "openai"},
            "response": {"error": "timeout"},
            "parsed": {"analysis": "Timeout"},
        }

    async def fake_save(self, *a, **k):
        saved.append(a)
        from app.models import AIAnalytics

        return AIAnalytics(id=1, source_id=source.id)

    monkeypatch.setattr(AIAnalyzer, "_analyze_text", fake_analyze_text)
    monkeypatch.setattr(AIAnalyzer, "_save_analysis", fake_save)
    # content must be classified as text-only (no media URLs / images).
    monkeypatch.setattr(ContentClassifier, "prepare_text_content", lambda items: "text")
    monkeypatch.setattr(ContentClassifier, "get_media_urls", lambda items: [])

    result = await AIAnalyzer().base_analyze_content(
        [{"text": "post 1", "published_at": "2026-10-03T10:00:00"}], source
    )
    assert result is None
    assert saved == [], "a timed-out LLM stub must not be persisted"


async def test_analyze_content_uses_passed_scenario_when_forwarded(monkeypatch, source):
    """A caller-supplied scenario is forwarded to the analysis method."""
    from app.models import AgentScenario

    mode_called = []

    async def fake_by_themes(self, content, source, force_reanalyze=False, agent_scenario=None, trigger_config=None, task_payload=None, **kwargs):
        mode_called.append(agent_scenario)
        return []

    monkeypatch.setattr(AIAnalyzer, "_analyze_content_by_themes", fake_by_themes)

    sc = AgentScenario(id=999, name="Сценарий 6", analysis_types=["sentiment"])
    await AIAnalyzer().analyze_content(
        [{"text": "x"}], source,
        agent_scenario=sc,
    )
    assert mode_called == [sc]


async def test_base_analyze_prefers_passed_scenario_over_tenant_default(monkeypatch, source):
    """`base_analyze_content` must use the passed scenario, not re-lookup the default."""
    from app.models import AgentScenario
    from app.services.ai.content_classifier import ContentClassifier

    looked_up_default = []

    async def fake_default(tenant_id=None):
        looked_up_default.append(tenant_id)
        return AgentScenario(id=1, name="Default")

    monkeypatch.setattr(AgentScenario.objects, "get_default_scenario", fake_default)
    monkeypatch.setattr(AIAnalyzer, "_save_analysis", lambda *a, **k: None)
    monkeypatch.setattr(ContentClassifier, "prepare_text_content", lambda items: "text")
    monkeypatch.setattr(ContentClassifier, "get_media_urls", lambda items: [])
    monkeypatch.setattr(AIAnalyzer, "_get_llm_model", lambda *a, **k: None)

    sc = AgentScenario(id=999, name="Task scenario", output_schema=None, analysis_types=["sentiment"])
    await AIAnalyzer().base_analyze_content(
        [{"text": "post 1", "published_at": "2026-10-03T10:00:00"}], source, agent_scenario=sc
    )
    # No tenant-default lookup happened: the caller's scenario was already resolved.
    assert looked_up_default == []
