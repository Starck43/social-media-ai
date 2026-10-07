"""Tests for the analytics enums: AnalysisType coverage and AnalyzeType binding.

`AnalyzeType` was a bare `Enum` of plain strings, so `sa_column()` produced a
plain VARCHAR: an ORM write of an enum member raised
`LookupError: 'AnalyzeType.THEMES' is not among the defined enum values`. These
tests pin the tuple shape, the member <-> string round trip and the read side,
so the web scenario editor can save an analysis mode again.
"""

import uuid

import sqlalchemy as sa

from app.models import AgentScenario
from app.types import AnalyzeType, AnalysisType, ContentType, MediaType, PeriodType, SentimentLabel
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


# --- AnalyzeType -----------------------------------------------------------


def test_analyze_type_has_four_modes():
    assert {m.db_value for m in AnalyzeType} == {"themes", "days", "sources", "monitored_users"}


def test_analyze_type_stores_the_same_db_values_as_the_migration():
    """Migration 0070 created the PostgreSQL `analyze_type` type with these
    strings; the enum must not drift from them or writes fail at the DB."""
    assert AnalyzeType.get_db_values(store_as_name=False) == ["themes", "days", "sources", "monitored_users"]


# --- neighbouring enums -----------------------------------------------------


def test_content_types_and_required_media():
    assert {c.db_value for c in ContentType} == {
        "posts",
        "comments",
        "videos",
        "stories",
        "reels",
        "reactions",
        "mentions",
    }
    assert ContentType.VIDEOS.required_media_type == "video"
    assert ContentType.COMMENTS.required_media_type == "text"


def test_media_type_lookup_accepts_name_or_db_value():
    assert MediaType.get_by_name("VIDEO") is MediaType.VIDEO
    assert MediaType.get_by_name("video") is MediaType.VIDEO
    assert MediaType.get_by_name("nope") is None


def test_sentiment_labels_and_periods():
    assert {s.db_value for s in SentimentLabel} == {"positive", "negative", "neutral", "mixed"}
    assert {p.db_value for p in PeriodType} == {"day", "week", "month", "custom"}
    assert str(PeriodType.WEEK) == "Еженедельно"


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
