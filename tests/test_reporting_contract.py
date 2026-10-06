"""The contract boundary between `JSONSchemaBuilder` and `ReportAggregator`.

Stage 2 renamed and retyped the analysis fields; the aggregator reads what the
LLM wrote. That makes the two halves of the same feature, and a rename that does
not touch the reader silently empties the matching aggregation — the LLM starts
writing `toxicity_level` while the reader only looks for `toxicity_category`, and
the digest section quietly reports nothing.

So every test here pairs a field with the name the *current* schema asks for, and
`test_aggregator_reads_every_field_the_schema_produces` is the guard: it derives
the list from `ANALYSIS_TYPE_SCHEMAS` instead of restating it, so the next rename
cannot pass unnoticed.

The legacy rows (the previous field names) are still expected to aggregate —
stored analyses are not rewritten by a contract change.
"""

import uuid

import pytest

from app.models import AgentScenario, AIAnalytics, Platform, Source
from app.services.ai.json_schema_builder import JSONSchemaBuilder
from app.services.ai.reporting import ReportAggregator
from app.types import PeriodType, PlatformType, SourceType

# current schema field -> the aggregation that reads it. Only fields the
# *specialized* aggregations (the stage-3 ones) consume are listed; sentiment,
# topics, keywords and engagement have their own long-standing aggregations
# above, so listing them here would claim a coverage this file does not test.
SCHEMA_FIELDS = {
    "toxicity_score": "toxicity",
    "toxicity_level": "toxicity",
    "brand": "brand_mentions",
    "context": "brand_mentions",
    "sentiment": "brand_mentions",
    "viral_score": "viral",
    "viral_factors": "viral",
    "predicted_reach": "viral",
    "trend_name": "trends",
    "growth_rate": "trends",
    "momentum": "trends",
    "intent_type": "intent",
    "confidence": "intent",
    "influencer_name": "influencer",
    "reach": "influencer",
    "impact_score": "influencer",
    "competitor": "competitor",
    "activity_type": "competitor",
    "threat_level": "competitor",
    "age_range": "demographics",
    "gender_dist": "demographics",
    "top_locations": "demographics",
    "hashtags": "hashtags",
}

# Read by an aggregation outside `DIGEST_SPECIALIZED` (see above), or by the
# chains tool/grouping rather than a specialized digest section.
FIELDS_OWNED_ELSEWHERE = {
    "sentiment_score",
    "sentiment_label",
    "main_topics",
    "entities",
    "keywords",
    "likes",
    "comments",
    "shares",
    "engagement_rate",
}
from datetime import date, timedelta

import pytest

from app.models import AIAnalytics, Platform, Source
from app.services.ai.json_schema_builder import JSONSchemaBuilder
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
        name="Contract Source",
        source_type=SourceType.CHANNEL,
        external_id="contract-test",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


@pytest.fixture(autouse=True)
async def _db_cleanup():
    await AIAnalytics.objects.delete()
    yield
    await AIAnalytics.objects.delete()


def _summary(text: dict | None = None, scenario_id: int | None = None) -> dict:
    data: dict = {"multi_llm_analysis": {"text_analysis": text or {}}}
    if scenario_id is not None:
        data["scenario_metadata"] = {"scenario_id": scenario_id}
    return data


async def _seed_raw(source, summaries: list[dict]):
    """One analytics row per summary, on distinct dates (the unique constraint
    forbids two DAILY rows on the same date)."""
    for i, summary in enumerate(summaries):
        await AIAnalytics.objects.create(
            source_id=source.id,
            analysis_date=date.today() - timedelta(days=i),
            period_type=PeriodType.DAY,
            summary_data=summary,
        )


# ── the guard ───────────────────────────────────────────────────────────────


def test_every_schema_field_is_claimed_by_a_contract_test():
    """Derived from the schema, not restated: a field the schema adds and this
    file does not account for fails here instead of being silently unread."""
    produced = {f for fields in JSONSchemaBuilder.ANALYSIS_TYPE_SCHEMAS.values() for f in fields}
    unknown = produced - set(SCHEMA_FIELDS) - FIELDS_OWNED_ELSEWHERE
    assert not unknown, f"contract tests do not cover schema fields: {sorted(unknown)}"


def test_exempted_fields_are_real_schema_fields():
    """Keeps the exemption list honest: it may only name real schema fields."""
    produced = {f for fields in JSONSchemaBuilder.ANALYSIS_TYPE_SCHEMAS.values() for f in fields}
    assert FIELDS_OWNED_ELSEWHERE <= produced, sorted(FIELDS_OWNED_ELSEWHERE - produced)


def test_aliases_point_at_real_fields():
    """A `FIELD_ALIASES` entry must list the current name first — that is the one
    the schema produces, and the reader must prefer it."""
    produced = {f for fields in JSONSchemaBuilder.ANALYSIS_TYPE_SCHEMAS.values() for f in fields}
    for logical, aliases in ReportAggregator.FIELD_ALIASES.items():
        assert aliases, logical
        assert aliases[0] in produced, f"{logical}: current name {aliases[0]!r} is not in the schema"


async def test_renaming_a_schema_field_empties_the_aggregation(source):
    """What this whole file is about: the reader follows the schema's names.

    Written against the current names, so a rename of, say, `toxicity_level`
    breaks it — which is the moment to add the alias, rather than shipping a
    digest section that reports nothing.
    """
    await _seed_raw(source, [_summary({"toxicity_score": 0.9, "toxicity_level": "high"})])
    result = await ReportAggregator().get_toxicity_summary(days=7)
    assert result["analyzed"] == 1
    assert result["categories"] == {"high": 1}


async def test_toxicity_level_outranks_a_stale_score(source):
    """The level and the score describe the same judgment, but they come from
    separate prompts, so a row can carry a stale score next to a fresh level.
    Reading the score would report a "low" row as toxic."""
    await _seed_raw(source, [_summary({"toxicity_score": 0.9, "toxicity_level": "low"})])
    result = await ReportAggregator().get_toxicity_summary(days=7)
    assert result["toxic"] == 0
    assert result["categories"] == {"low": 1}


# ── per-type: the current contract ──────────────────────────────────────────


async def test_hashtags_read_the_object_form(source):
    """The schema asks for `{"tag": ..., "count": ..., "sentiment": ...}`, not a
    bare string — a string-only reader would report no hashtags at all."""
    await _seed_raw(source, [_summary({"hashtags": [{"tag": "релиз", "count": 5, "sentiment": 0.8}]})])
    result = await ReportAggregator().get_top_hashtags(days=7)
    assert result == [{"hashtag": "релиз", "count": 1}]


async def test_hashtags_still_accept_bare_strings(source):
    await _seed_raw(source, [_summary({"hashtags": ["#релиз", "#релиз", "#баг"]})])
    result = await ReportAggregator().get_top_hashtags(days=7)
    assert result[0] == {"hashtag": "#релиз", "count": 2}


async def test_brand_mentions_read_brand_and_numeric_sentiment(source):
    """The new contract gives a brand name and a 0..1 score, so the sentiment
    split is counted from the score — `mention_count` no longer exists."""
    await _seed_raw(
        source,
        [
            _summary({"brand": "Acme", "context": "в посте", "sentiment": 0.9}),
            _summary({"brand": "Acme", "context": "в комменте", "sentiment": 0.1}),
            _summary({"brand": "Acme", "context": "нейтрально", "sentiment": 0.5}),
        ],
    )
    result = await ReportAggregator().get_brand_mention_stats(days=7)
    assert result["rows_with_mentions"] == 3
    assert result["total_mentions"] == 3
    assert result["sentiment_split"] == {"positive": 1, "neutral": 1, "negative": 1}


async def test_brand_mentions_still_sum_the_legacy_count(source):
    """A legacy row counts several mentions in one analysis; the new contract
    writes one brand per analysis, so both shapes have to keep counting."""
    await _seed_raw(source, [_summary({"mention_count": 4, "mention_sentiment": "positive"})])
    result = await ReportAggregator().get_brand_mention_stats(days=7)
    assert result["total_mentions"] == 4
    assert result["sentiment"] == {"positive": 1}


async def test_viral_ranks_a_numeric_score(source):
    """`viral_score` is a number now, not a label, so it is ranked on its value."""
    await _seed_raw(
        source,
        [
            _summary({"viral_score": 0.2, "viral_factors": ["фактор"], "predicted_reach": 100}),
            _summary({"viral_score": 0.9, "viral_factors": ["фактор"], "predicted_reach": 5000}),
        ],
    )
    result = await ReportAggregator().get_viral_content(days=7)
    assert [r["viral_potential"] for r in result] == [0.9, 0.2]
    assert result[0]["rank"] == 3
    assert result[1]["rank"] == 1


async def test_viral_still_ranks_a_legacy_label(source):
    await _seed_raw(
        source,
        [
            _summary({"viral_potential": "Низкий", "growth_rate": 0.5}),
            _summary({"viral_potential": "Высокий", "growth_rate": 2.5}),
        ],
    )
    result = await ReportAggregator().get_viral_content(days=7)
    assert result[0]["viral_potential"] == "Высокий"
    assert result[0]["rank"] == 3


async def test_intent_reads_type_and_confidence(source):
    await _seed_raw(
        source,
        [
            _summary({"intent_type": "purchase", "confidence": 0.8}),
            _summary({"intent_type": "purchase", "confidence": 0.6}),
            _summary({"intent_type": "complaint", "confidence": 0.4}),
        ],
    )
    result = await ReportAggregator().get_intent_distribution(days=7)
    assert result["distribution"]["purchase"] == 2
    assert result["total"] == 3
    assert result["avg_confidence"] == 0.6


async def test_intent_still_counts_secondary_intents(source):
    """A legacy row has primary + secondary; the primary must not be mistaken for
    the current `intent_type` and cut the secondary list short."""
    await _seed_raw(source, [_summary({"primary_intent": "жалоба", "secondary_intents": ["вопрос", "жалоба"]})])
    result = await ReportAggregator().get_intent_distribution(days=7)
    assert result["distribution"]["жалоба"] == 2
    assert result["distribution"]["вопрос"] == 1


async def test_influencer_reads_the_flat_contract(source):
    await _seed_raw(source, [_summary({"influencer_name": "@star", "reach": 1000, "impact_score": 0.9})])
    result = await ReportAggregator().get_influencer_impact(days=7)
    assert result[0]["name"] == "@star"
    assert result[0]["impact"] == 0.9


async def test_influencer_keeps_a_word_label(source):
    """An impact that is a word has nothing to sum, so it is passed through."""
    await _seed_raw(source, [_summary({"influencers": [{"name": "@star", "impact": "высокое"}]})])
    result = await ReportAggregator().get_influencer_impact(days=7)
    assert result[0]["impact"] == "высокое"


async def test_influencer_mixed_shapes_sort_without_a_type_error(source):
    """A numeric and a worded impact in one result set must not be compared
    against each other."""
    await _seed_raw(
        source,
        [
            _summary({"influencer_name": "@a", "impact_score": 0.9}),
            _summary({"influencers": [{"name": "@b", "impact": "среднее"}]}),
        ],
    )
    result = await ReportAggregator().get_influencer_impact(days=7)
    assert [r["name"] for r in result] == ["@a", "@b"]


async def test_competitor_reads_the_flat_contract(source):
    await _seed_raw(
        source,
        [
            _summary({"competitor": "Конкурент А", "activity_type": "launch", "threat_level": "high"}),
            _summary({"competitor": "Конкурент А", "activity_type": "promotion", "threat_level": "high"}),
        ],
    )
    result = await ReportAggregator().get_competitor_activity(days=7)
    assert result["total"] == 2
    assert result["competitors"][0] == {"name": "Конкурент А", "mentions": 2}
    assert result["threat_levels"] == {"high": 2}


async def test_demographics_reads_the_flat_contract(source):
    await _seed_raw(
        source,
        [_summary({"age_range": "25-34", "gender_dist": {"female": 60}, "top_locations": ["Москва", "СПб"]})],
    )
    result = await ReportAggregator().get_demographics_breakdown(days=7)
    assert result["age_groups"] == {"25-34": 1}
    assert result["locations"] == {"Москва": 1, "СПб": 1}


async def test_demographics_still_reads_the_nested_shape(source):
    await _seed_raw(source, [_summary({"demographics": {"age_groups": ["18-24"], "locations": ["Москва"]}})])
    result = await ReportAggregator().get_demographics_breakdown(days=7)
    assert result["age_groups"] == {"18-24": 1}
    assert result["locations"] == {"Москва": 1}


# ── scenario scoping ────────────────────────────────────────────────────────


async def test_scenario_filter_narrows_to_that_scenario(source):
    await _seed_raw(
        source,
        [_summary({"toxicity_score": 0.9}, scenario_id=1), _summary({"toxicity_score": 0.1}, scenario_id=2)],
    )
    agg = ReportAggregator()
    assert (await agg.get_toxicity_summary(days=7, scenario_id=1))["analyzed"] == 1
    assert (await agg.get_toxicity_summary(days=7, scenario_id=2))["analyzed"] == 1
    assert (await agg.get_toxicity_summary(days=7))["analyzed"] == 2


async def test_rows_without_scenario_metadata_match_no_scenario(source):
    """An analysis produced before the metadata existed was not produced by a
    scenario, so it must not be counted as one."""
    await _seed_raw(source, [_summary({"toxicity_score": 0.9}), _summary({"toxicity_score": 0.5}, scenario_id=1)])
    result = await ReportAggregator().get_toxicity_summary(days=7, scenario_id=1)
    assert result["analyzed"] == 1


async def test_every_specialized_method_accepts_scenario_id():
    """The stage-3 contract: source_id, days and an optional scenario_id on each."""
    import inspect

    for _label, name in ReportAggregator.DIGEST_SPECIALIZED.values():
        method = getattr(ReportAggregator, name, None)
        assert method is not None, name
        params = inspect.signature(method).parameters
        assert "scenario_id" in params, name
        assert "days" in params, name


# ── chain fields the schema now asks for ──────────────────────────────────────


def test_common_fields_include_the_chain_fields():
    """COMMON_FIELDS carries the chain of relevance fields the analyzer reads
    for `_save_analysis` filtering and `_resolve_chain_label`."""
    for field in ("topic_hint", "confidence", "is_meaningful"):
        assert field in JSONSchemaBuilder.COMMON_FIELDS, field


def test_entities_appear_in_the_schema_for_types_that_need_it():
    schema = JSONSchemaBuilder.build_schema(["topics", "trends"], {})
    assert "entities" in schema
    assert "person|brand|org" in schema["entities"]
