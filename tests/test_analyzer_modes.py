"""Tests for the expanded AgentScenario.analyze_type modes.

Verifies the analyzer dispatches on all four modes (themes/days/sources/
monitored_users) and that the new modes group content and build stable chain
ids correctly. `base_analyze_content` is stubbed — no LLM, no DB writes.
"""

import uuid

import pytest

from app.models import Platform, Source
from app.services.ai.analyzer import AIAnalyzer
from app.types import PlatformType, SourceType
from app.types.enums.bot_types import AnalyzeType


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


def _content(*authors):
    """Content items with distinct author ids."""
    return [{"text": f"post {i}", "author_id": a} for i, a in enumerate(authors)]


async def test_analyze_type_enum_has_four_modes():
    # `db_value`, not `.value`: the member value is the (db_value, label,
    # emoji) tuple, and the column stores the db_value string.
    assert {m.db_value for m in AnalyzeType} == {"themes", "days", "sources", "monitored_users"}


async def test_analyze_content_dispatches_sources_mode(monkeypatch, source):
    """sources mode groups content by origin source and builds a per-source chain."""
    seen = {}

    async def fake_base(self, content, source, topic_chain_id=None, parent_analysis_id=None,
                        analysis_date=None, force_reanalyze=False, analyze_type=None,
                        agent_scenario=None, trigger_config=None, task_payload=None, **kwargs):
        seen["chain"] = topic_chain_id
        seen["analyze_type"] = analyze_type
        seen["len"] = len(content)
        from app.models import AIAnalytics

        return AIAnalytics(id=1, source_id=source.id)

    monkeypatch.setattr(AIAnalyzer, "base_analyze_content", fake_base)

    content = [
        {"text": "a", "source_id": 10},
        {"text": "b", "source_id": 10},
        {"text": "c", "source_id": 20},
    ]
    result = await AIAnalyzer().analyze_content(content, source, analyze_by="sources")
    # Two groups (source 10, source 20).
    assert len(result) == 2
    # The mode is forwarded so the real base_analyze_content can build a
    # per-source stable chain (src_{id}_def_all) instead of a topic-anchored one.
    assert seen["analyze_type"] == "sources"


async def test_analyze_content_dispatches_monitored_users_mode(monkeypatch, source):
    """monitored_users mode groups content by author with a per-user chain."""
    chains = []

    async def fake_base(self, content, source, topic_chain_id=None, parent_analysis_id=None,
                        analysis_date=None, force_reanalyze=False, analyze_type=None,
                        agent_scenario=None, trigger_config=None, task_payload=None, **kwargs):
        chains.append(topic_chain_id)
        from app.models import AIAnalytics

        return AIAnalytics(id=1, source_id=source.id)

    monkeypatch.setattr(AIAnalyzer, "base_analyze_content", fake_base)

    content = _content(100, 100, 200)
    result = await AIAnalyzer().analyze_content(content, source, analyze_by="monitored_users")
    assert len(result) == 2
    # One stable chain per monitored user.
    assert chains == [f"src_{source.id}_user_100", f"src_{source.id}_user_200"]


async def test_monitored_users_groups_by_author_dict(monkeypatch, source):
    """An `author` dict (platform-client shape) keys the per-user grouping too."""
    chains = []

    async def fake_base(self, content, source, topic_chain_id=None, parent_analysis_id=None,
                      analysis_date=None, force_reanalyze=False, analyze_type=None,
                      agent_scenario=None, trigger_config=None, task_payload=None, **kwargs):
        chains.append(topic_chain_id)
        from app.models import AIAnalytics

        return AIAnalytics(id=1, source_id=source.id)

    monkeypatch.setattr(AIAnalyzer, "base_analyze_content", fake_base)

    content = [
        {"text": "a", "author": {"id": 1}},
        {"text": "b", "author": {"id": 2}},
        {"text": "c", "author": {"id": 1}},
    ]
    await AIAnalyzer().analyze_content(content, source, analyze_by="monitored_users")
    assert sorted(chains) == [f"src_{source.id}_user_1", f"src_{source.id}_user_2"]


async def test_analyze_content_days_mode_groups_by_day(monkeypatch, source):
    """days mode still routes to the day-grouped analysis (regression guard)."""
    from app.services.ai.analyzer import AIAnalyzer

    called = []

    async def fake_by_days(self, content, source, force_reanalyze=False, agent_scenario=None, trigger_config=None, task_payload=None, **kwargs):
        called.append("days")
        return []

    monkeypatch.setattr(AIAnalyzer, "_analyze_content_by_days", fake_by_days)

    await AIAnalyzer().analyze_content([{"text": "x"}], source, analyze_by="days")
    assert called == ["days"]


async def test_analyze_content_themes_mode_still_works(monkeypatch, source):
    """themes mode still routes to the theme-grouped analysis (regression guard)."""
    called = []

    async def fake_by_themes(self, content, source, force_reanalyze=False, agent_scenario=None, trigger_config=None, task_payload=None, **kwargs):
        called.append("themes")
        return []

    monkeypatch.setattr(AIAnalyzer, "_analyze_content_by_themes", fake_by_themes)

    await AIAnalyzer().analyze_content([{"text": "x"}], source, analyze_by="themes")
    assert called == ["themes"]


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


async def test_analyze_content_uses_passed_scenario_for_mode(monkeypatch, source):
    """A caller-supplied scenario's analyze_type selects the mode and is forwarded.

    This is the regression behind "no agent conclusion / old unclear chain title":
    `handle_analyze` resolves the task's scenario but used to drop it, so the
    analysis fell back to the (absent) tenant default and the default prompt —
    scenario 6's custom prompt/analysis_types never ran. Now the resolved
    scenario reaches `base_analyze_content`, which must use it instead of the
    tenant default.
    """
    from app.models import AgentScenario

    mode_called = []

    async def fake_by_days(self, content, source, force_reanalyze=False, agent_scenario=None, trigger_config=None, task_payload=None, **kwargs):
        mode_called.append(agent_scenario)
        return []

    monkeypatch.setattr(AIAnalyzer, "_analyze_content_by_days", fake_by_days)

    sc = AgentScenario(id=999, name="Сценарий 6", analyze_type="days")
    await AIAnalyzer().analyze_content([{"text": "x"}], source, agent_scenario=sc)
    # The scenario's analyze_type ("days") selects the mode, and the scenario
    # object itself is handed down.
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
