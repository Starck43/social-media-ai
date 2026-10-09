"""Hybrid digest: ReportAggregator's specialized aggregations and the brief.

Covers the algorithmic half of the hybrid digest — the part that must work
without an LLM call:
- the 8 specialized aggregation methods (toxicity, hashtags, brand mentions,
  viral, influencer, competitor, intent, demographics)
- `generate_digest_brief` grouping by `analyze_type`
  (themes / days / sources / monitored_users)
- the builder wiring: `aggregate()` carries the brief, `render_digest()` renders it
"""

import uuid
from datetime import date, timedelta

import pytest

from app.models import AIAnalytics, AgentScenario, Platform, Source
from app.services.ai.reporting import ReportAggregator
from app.types import PeriodType, PlatformType, SourceType


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
        name="Brief Test Source",
        source_type=SourceType.CHANNEL,
        external_id="brief-test",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


def _text(overrides: dict | None = None) -> dict:
    base = {
        "main_topics": ["релиз", "баги"],
        "toxicity_score": 0.8,
        "toxicity_category": "Очень токсичный",
        "mention_count": 5,
        "mention_sentiment": "positive",
        "viral_potential": "Высокий",
        "growth_rate": 2.5,
        "primary_intent": "жалоба",
        "secondary_intents": ["вопрос"],
        "hashtags": ["#релиз", "#обновление"],
        "demographics": {"age_groups": ["18-24"], "locations": ["Москва"], "interests": ["IT"]},
        "analysis_title": "Активность за день",
    }
    if overrides:
        base.update(overrides)
    return base


def _summary(**overrides) -> dict:
    data = {
        "analysis_title": "Активность за день",
        "analysis_summary": "Общая сводка",
        "multi_llm_analysis": {"text_analysis": _text()},
        "content_statistics": {
            "total_posts": 10,
            "messages_count": 40,
            "active_users": 5,
            "total_reactions": 20,
            "total_comments": 3,
            "total_views": 500,
        },
    }
    data.update(overrides)
    return data


async def _cleanup():
    await AIAnalytics.objects.delete()
    await Source.objects.delete()
    await AgentScenario.objects.delete()


@pytest.fixture(autouse=True)
async def _db_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _seed(source, rows: list[dict]):
    # distinct dates per row — the unique (source_id, analysis_date,
    # period_type) constraint forbids two DAILY rows on the same date
    for i, row in enumerate(rows):
        await AIAnalytics.objects.create(
            source_id=source.id,
            analysis_date=row.get("analysis_date", date.today() - timedelta(days=i)),
            period_type=PeriodType.DAY,
            summary_data=row["summary_data"],
            topic_chain_id=row.get("topic_chain_id"),
        )


# ── specialized aggregations ────────────────────────────────────────────────


async def test_toxicity_summary(source):
    await _seed(
        source,
        [
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text()})},
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"toxicity_level": "low"})})},
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": {}})},
        ],
    )
    result = await ReportAggregator().get_toxicity_summary(days=7)
    assert result["analyzed"] == 2
    assert result["toxic"] == 1
    assert result["toxic_percent"] == 50.0
    assert result["categories"]["Очень токсичный"] == 1
    assert result["categories"]["low"] == 1


async def test_top_hashtags(source):
    await _seed(
        source,
        [
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text()})},
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"hashtags": ["#релиз"]})})},
        ],
    )
    result = await ReportAggregator().get_top_hashtags(days=7)
    assert result[0]["hashtag"] == "#релиз"
    assert result[0]["count"] == 2


async def test_brand_mention_stats(source):
    await _seed(
        source,
        [
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text()})},
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"mention_count": 3})})},
        ],
    )
    result = await ReportAggregator().get_brand_mention_stats(days=7)
    assert result["total_mentions"] == 8
    assert result["rows_with_mentions"] == 2
    assert result["sentiment"]["positive"] == 2


async def test_viral_content_sorted(source):
    await _seed(
        source,
        [
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"viral_potential": "Высокий", "growth_rate": 2.5})})},
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"viral_potential": "Низкий", "growth_rate": 0.5})})},
        ],
    )
    result = await ReportAggregator().get_viral_content(days=7)
    assert len(result) == 2
    assert result[0]["viral_potential"] == "Высокий"
    assert result[0]["growth_rate"] == 2.5


async def test_influencer_impact_empty_without_data(source):
    await _seed(source, [{"summary_data": _summary()}])
    result = await ReportAggregator().get_influencer_impact(days=7)
    assert result == []


async def test_influencer_impact_parses_list(source):
    text = _text({"influencers": [{"name": "@star", "impact": "высокое"}]})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_influencer_impact(days=7)
    assert result[0]["name"] == "@star"
    assert result[0]["impact"] == "высокое"


async def test_competitor_activity(source):
    text = _text({"competitors": ["Конкурент А", "Конкурент А"]})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_competitor_activity(days=7)
    assert result["total"] == 2
    assert result["competitors"][0]["name"] == "Конкурент А"


async def test_intent_distribution(source):
    text = _text({"primary_intent": "жалоба", "secondary_intents": ["вопрос", "жалоба"]})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_intent_distribution(days=7)
    assert result["distribution"]["жалоба"] == 2
    assert result["distribution"]["вопрос"] == 1


async def test_demographics_breakdown(source):
    await _seed(source, [{"summary_data": _summary()}])
    result = await ReportAggregator().get_demographics_breakdown(days=7)
    assert result["age_groups"]["18-24"] == 1
    assert result["locations"]["Москва"] == 1
    assert result["interests"]["IT"] == 1


# ── new JSONSchemaBuilder contract field names ───────────────────────────────
# The typed contract (ANALYSIS_TYPE_SCHEMAS) renamed several fields. These pin
# that FIELD_ALIASES keeps the aggregations reading rows written after the rename.


def _new_contract_text(overrides: dict | None = None) -> dict:
    base = {
        "main_topics": ["релиз"],
        "toxicity_score": 0.9,
        "toxicity_level": "high",
        "brand": "Acme",
        "context": "новый продукт",
        "sentiment": 0.8,
        "viral_score": 0.85,
        "predicted_reach": 12000,
        "influencer_name": "@star",
        "impact_score": 0.7,
        "competitor": "Конкурент Б",
        "threat_level": "high",
        "intent_type": "complaint",
        "confidence": 0.8,
        "hashtags": [{"tag": "релиз", "count": 3, "sentiment": 0.8}],
        "age_range": "25-34",
        "top_locations": ["СПб", "Москва"],
        "analysis_title": "Новый контракт",
    }
    if overrides:
        base.update(overrides)
    return base


async def test_toxicity_level_maps_to_score(source):
    await _seed(
        source,
        [
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _new_contract_text()})},
            {"summary_data": _summary(multi_llm_analysis={"text_analysis": _new_contract_text({"toxicity_level": "low"})})},
        ],
    )
    result = await ReportAggregator().get_toxicity_summary(days=7)
    assert result["analyzed"] == 2
    assert result["toxic"] == 1  # high → toxic, low → clean
    assert result["categories"]["high"] == 1
    assert result["categories"]["low"] == 1


async def test_hashtag_objects_are_counted(source):
    text = _new_contract_text({"hashtags": [{"tag": "релиз", "count": 3}, {"tag": "вакансии", "count": 1}]})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_top_hashtags(days=7)
    assert result[0]["hashtag"] == "релиз"


async def test_brand_mentions_new_contract_sentiment_split(source):
    text = _new_contract_text({"sentiment": 0.8})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_brand_mention_stats(days=7)
    assert result["total_mentions"] == 1
    assert result["sentiment_split"]["positive"] == 1


async def test_viral_score_new_contract(source):
    text = _new_contract_text({"viral_score": 0.85})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_viral_content(days=7)
    assert len(result) == 1
    assert result[0]["predicted_reach"] == 12000


async def test_influencer_flat_new_contract(source):
    text = _new_contract_text({"influencer_name": "@star", "impact_score": 0.7})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_influencer_impact(days=7)
    assert result[0]["name"] == "@star"


async def test_competitor_flat_new_contract(source):
    text = _new_contract_text({"competitor": "Конкурент Б", "threat_level": "high"})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_competitor_activity(days=7)
    assert result["total"] == 1
    assert result["competitors"][0]["name"] == "Конкурент Б"
    assert result["threat_levels"]["high"] == 1


async def test_intent_type_new_contract(source):
    text = _new_contract_text({"intent_type": "complaint", "confidence": 0.8})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_intent_distribution(days=7)
    assert result["distribution"]["complaint"] == 1
    assert result["avg_confidence"] == 0.8


async def test_demographics_flat_new_contract(source):
    text = _new_contract_text({"age_range": "25-34", "top_locations": ["СПб", "Москва"]})
    await _seed(source, [{"summary_data": _summary(multi_llm_analysis={"text_analysis": text})}])
    result = await ReportAggregator().get_demographics_breakdown(days=7)
    assert result["age_groups"]["25-34"] == 1
    assert result["locations"]["СПб"] == 1
    assert result["locations"]["Москва"] == 1


# ── generate_digest_brief grouping ──────────────────────────────────────────


async def test_brief_groups_by_themes(source):
    await _seed(source, [{"summary_data": _summary()}])
    brief = await ReportAggregator().generate_digest_brief(period="day", group_by="themes")
    assert "## По темам" in brief
    assert "релиз" in brief


async def test_brief_groups_by_days(source):
        await _seed(
            source,
            [
                {"summary_data": _summary(), "analysis_date": date.today()},
                {"summary_data": _summary(), "analysis_date": date.today() - timedelta(days=1)},
            ],
        )
        brief = await ReportAggregator().generate_digest_brief(period="week", group_by="themes", time_breakdown=True)
        assert "## По темам" in brief


async def test_brief_groups_by_sources(source):
    await _seed(source, [{"summary_data": _summary()}])
    brief = await ReportAggregator().generate_digest_brief(period="day", group_by="sources")
    assert "## По источникам" in brief
    assert "Brief Test Source" in brief


async def test_brief_groups_by_monitored_users(source):
        await _seed(
            source,
            [
                {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"entities": [{"name": "person1", "type": "person"}]})}), "topic_chain_id": f"src_{source.id}_user_person1"},
                {"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"entities": [{"name": "person2", "type": "person"}]})}), "topic_chain_id": f"src_{source.id}_user_person2"},
            ],
        )
        brief = await ReportAggregator().generate_digest_brief(period="day", group_by="entities", entity_type="person")
        assert "## По упоминаниям" in brief


async def test_brief_returns_empty_when_no_analytics():
    brief = await ReportAggregator().generate_digest_brief(period="day", group_by="themes")
    assert brief == ""


async def test_brief_filters_to_source_ids(source):
    other = await Source.objects.create(
        platform_id=source.platform_id,
        name="Other Source",
        source_type=SourceType.CHANNEL,
        external_id="other-brief",
        is_active=True,
    )
    try:
        await _seed(
            source,
            [{"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"main_topics": ["только-этот"]})})}],
        )
        await _seed(
            other,
            [{"summary_data": _summary(multi_llm_analysis={"text_analysis": _text({"main_topics": ["другой"]})})}],
        )
        brief = await ReportAggregator().generate_digest_brief(period="day", group_by="themes", source_ids=[source.id])
        assert "только-этот" in brief
        assert "другой" not in brief
    finally:
        await Source.objects.delete_by_id(other.id)


async def test_brief_scenario_controls_specialized_sections(source):
    scenario = await AgentScenario.objects.create(
        name="toxicity-only",
        analysis_types=["toxicity"],
    )
    try:
        # The scenario filter matches rows through summary_data.scenario_metadata
        # (ai_analytics has no scenario column), so the seeded row must carry it.
        summary = _summary()
        summary["scenario_metadata"] = {"scenario_id": scenario.id, "scenario_name": "toxicity-only"}
        await _seed(source, [{"summary_data": summary}])
        brief = await ReportAggregator().generate_digest_brief(period="day", group_by="themes", scenario_id=scenario.id)
        assert "## Токсичность" in brief
        assert "## Хэштеги" not in brief
        assert "## Упоминания бренда" not in brief
    finally:
        await AgentScenario.objects.delete_by_id(scenario.id)


# ── builder wiring: aggregate() + render_digest() ───────────────────────────


async def test_aggregate_carries_brief_and_render_converts_markdown(source):
    await _seed(source, [{"summary_data": _summary()}])
    from app.services.digest.builder import aggregate
    from app.services.digest.render import render_digest

    data, start, end = await aggregate("day")
    assert "brief" in data
    assert "## По темам" in data["brief"]

    rendered = render_digest(data, summary="Сводка")
    assert "<b>По темам</b>" in rendered
    assert "<b>релиз</b>" in rendered
    assert "<blockquote>Сводка</blockquote>" in rendered
