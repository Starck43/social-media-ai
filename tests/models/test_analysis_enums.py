"""Tests for the analytics enums: AnalysisType coverage.

`AnalyzeType` was removed - only query-time grouping (GroupingAxis) remains.
"""

import uuid

import sqlalchemy as sa

from app.models import AgentScenario
from app.types import AnalysisType, ContentType, MediaType, PeriodType, SentimentLabel
from app.types.enums.bot_types import BotTriggerType

# --- AnalysisType -----------------------------------------------------------


def test_analysis_type_covers_every_schema_family():
    """All 13 analysis types exist and their db_values are unique.

    The db_value is what a scenario stores in `analysis_types` (a JSON list of
    strings) and what `JSONSchemaBuilder.SCHEMA_FIELDS` is keyed by, so a
    duplicate or missing value would silently drop an analysis.
    """
    values = [a.db_value for a in AnalysisType]
    assert len(values) == len(set(values)), "duplicate db_value in AnalysisType"

    assert {
        "sentiment",
        "trends",
        "engagement",
        "keywords",
        "topics",
        "toxicity",
        "demographics",
        "viral_detection",
        "influencer",
        "competitor",
        "intent",
        "brand_mentions",
        "hashtag_analysis",
    } == set(values)


def test_specialized_analysis_types_have_expected_db_values():
    """The six specialized types use the names the pipeline keys on.

    `competitor` / `influencer` / `intent` are deliberately shorter than the
    member names: they are the stored values and the schema-builder keys, so a
    rename has to be a migration, not an edit.
    """
    assert AnalysisType.VIRAL_DETECTION.db_value == "viral_detection"
    assert AnalysisType.INFLUENCER_ACTIVITY.db_value == "influencer"
    assert AnalysisType.COMPETITOR_TRACKING.db_value == "competitor"
    assert AnalysisType.CUSTOMER_INTENT.db_value == "intent"
    assert AnalysisType.BRAND_MENTIONS.db_value == "brand_mentions"
    assert AnalysisType.HASHTAG_ANALYSIS.db_value == "hashtag_analysis"


def test_analysis_type_labels_carry_emoji_and_plain_name():
    assert AnalysisType.SENTIMENT.label == "😊 Анализ тональности"
    assert AnalysisType.SENTIMENT.label_no_emoji == "Анализ тональности"


def test_analysis_type_lookup_helpers():
    assert AnalysisType.get_by_value("intent") is AnalysisType.CUSTOMER_INTENT
    assert AnalysisType.get_by_name("HASHTAG_ANALYSIS") is AnalysisType.HASHTAG_ANALYSIS
    assert AnalysisType.get_by_value("nope") is None
    assert AnalysisType.get_by_name("NOPE") is None


def test_bot_trigger_type_matches_the_evaluator():
    """The evaluator and the enum must agree: TIME_BASED/MANUAL were removed in
    migration 0072 and nothing may select them again."""
    assert {t.db_value for t in BotTriggerType} == {
        "keyword_match",
        "sentiment_threshold",
        "activity_spike",
        "user_mention",
    }
    assert BotTriggerType.get_by_name("TIME_BASED") is None
    assert BotTriggerType.get_by_name("MANUAL") is None
