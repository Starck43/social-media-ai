"""Tests for the per-analysis-type response contract in `JSONSchemaBuilder`.

The contract is keyed by `AnalysisType.db_value`. Two bugs are pinned here:

- five analysis types (`competitor`, `demographics`, `hashtag_analysis`,
  `influencer`, `intent`) had no schema at all, so selecting one produced a
  prompt with nothing but the common fields;
- `user_intent` was described but `intent` — the actual db_value of
  `CUSTOMER_INTENT`, i.e. what the UI writes — was not, so "Намерения клиентов"
  resolved to nothing.

`test_every_analysis_type_has_a_schema` is the guard against the first returning.
"""

import json
import re

from app.services.ai.json_schema_builder import JSONSchemaBuilder
from app.services.ai.scenario import build_output_schema
from app.types import AnalysisType

# Every selectable type, used by the "nothing is left unresolved" check.
TYPES = [a.db_value for a in AnalysisType]

# The contract the checklist asks for, pinned per type.
EXPECTED_FIELDS = {
    "engagement": {"likes", "comments", "shares", "engagement_rate"},
    "toxicity": {"toxicity_score", "toxicity_level"},
    "demographics": {"age_range", "gender_dist", "top_locations"},
    "viral_detection": {"viral_score", "viral_factors", "predicted_reach"},
    "influencer": {"influencer_name", "reach", "impact_score"},
    "competitor": {"competitor", "activity_type", "threat_level"},
    "intent": {"intent_type", "confidence"},
    "brand_mentions": {"brand", "context", "sentiment"},
    "hashtag_analysis": {"hashtags"},
    "trends": {"trend_name", "growth_rate", "momentum", "entities"},
}


def test_every_analysis_type_has_a_schema():
    """The regression: every selectable type must describe at least one field."""
    for analysis_type in AnalysisType:
        key = analysis_type.db_value
        fields = JSONSchemaBuilder.fields_for(key)
        assert fields, f"{key} ({analysis_type.display_name}) has no response schema"


def test_the_new_types_describe_the_agreed_fields():
    for key, expected in EXPECTED_FIELDS.items():
        assert set(JSONSchemaBuilder.fields_for(key)) == expected, key


def test_customer_intent_is_reachable_under_its_db_value():
    """`AnalysisType.CUSTOMER_INTENT.db_value` is "intent", so that is the key
    the scenario stores and the builder must answer for."""
    assert AnalysisType.CUSTOMER_INTENT.db_value == "intent"
    assert JSONSchemaBuilder.fields_for(AnalysisType.CUSTOMER_INTENT.db_value)
    schema = JSONSchemaBuilder.build_schema([AnalysisType.CUSTOMER_INTENT.db_value])
    assert "intent_type" in schema and "confidence" in schema


def test_scores_declare_a_zero_to_one_range():
    """A score the model reads as an unbounded number is not comparable across
    analyses, so every 0..1 field says so."""
    for key, field in (
        ("viral_detection", "viral_score"),
        ("influencer", "impact_score"),
        ("intent", "confidence"),
        ("brand_mentions", "sentiment"),
    ):
        description = JSONSchemaBuilder.fields_for(key)[field][1]
        assert "0.0" in description and "1.0" in description, field


def test_legacy_types_still_describe_what_they_used_to():
    """Kept: no `AnalysisType` selects them, but an existing scenario may carry
    one in `analysis_types` and its stored analyses were made with them."""
    assert "primary_intent" in JSONSchemaBuilder.build_schema(["user_intent"])
    assert "context_type" in JSONSchemaBuilder.build_schema(["conversation_context"])
    assert "discussion_depth" in JSONSchemaBuilder.build_schema(["engagement_quality"])


# --- rendering the flat contract --------------------------------------------


def test_unknown_type_is_skipped_not_guessed():
    """A typo in a scenario must thin the prompt, not mis-describe it."""
    schema = JSONSchemaBuilder.build_schema(["definitely_not_a_type"])
    assert set(schema) == set(JSONSchemaBuilder.COMMON_FIELDS)


def test_build_schema_keeps_the_flat_shape_and_leads_with_common_fields():
    """The rendered contract is `{field: description}` and the common fields come
    first — the prompt has always looked like that."""
    schema = JSONSchemaBuilder.build_schema(["sentiment"])
    keys = list(schema)
    assert keys[: len(JSONSchemaBuilder.COMMON_FIELDS)] == list(JSONSchemaBuilder.COMMON_FIELDS)
    assert all(isinstance(v, str) for v in schema.values())


def test_descriptions_carry_their_type():
    """The prompt is prose, so the JSON type is folded into the description."""
    schema = JSONSchemaBuilder.build_schema(["engagement"])
    assert schema["likes"].startswith("integer,")
    assert schema["engagement_rate"].startswith("number,")
    assert schema["likes"].endswith("число лайков")


def test_a_field_two_types_declare_is_described_once():
    schema = JSONSchemaBuilder.build_schema(["toxicity", "trends", "viral_detection"])
    assert len(schema) == len(set(schema))
    assert "growth_rate" in schema


# --- scope resolution -------------------------------------------------------


def test_scope_overrides_the_list_length():
    scope = {"topics": {"max_topics": 9}, "keywords": {"max_keywords": 3}}
    schema = JSONSchemaBuilder.build_schema(["topics", "keywords"], scope)
    assert "из 9 главных тем" in schema["main_topics"]
    assert "из 3 ключевых слов" in schema["keywords"]


def test_scope_vocabulary_replaces_the_default():
    scope = {"toxicity": {"toxicity_levels": ["low", "critical"]}}
    schema = JSONSchemaBuilder.build_schema(["toxicity"], scope)
    assert '"low", "critical"' in schema["toxicity_level"]
    assert "medium" not in schema["toxicity_level"]


def test_scope_category_comes_from_the_type_own_config():
    scope = {"sentiment": {"categories": ["Позитивный", "Негативный"]}}
    schema = JSONSchemaBuilder.build_schema(["sentiment"], scope)
    assert "Позитивный" in schema["sentiment_label"]


def test_enum_placeholders_are_always_resolved():
    """No `{placeholder}` may survive into the prompt — an unresolved one tells
    the model nothing. (A `{` that is part of a JSON *example* in a description
    is fine; only a bare identifier in braces is a leak.)"""
    schema = JSONSchemaBuilder.build_schema(TYPES)
    leaked = [v for v in schema.values() if re.search(r"\{\w+\}", v)]
    assert not leaked, leaked


def test_a_null_scope_value_falls_back_to_the_default():
    scope = {"topics": {"max_topics": None}}
    schema = JSONSchemaBuilder.build_schema(["topics"], scope)
    assert str(JSONSchemaBuilder.DEFAULT_LIMITS["max_topics"]) in schema["main_topics"]


# --- event_based ------------------------------------------------------------


def test_event_based_is_read_from_scope():
    assert JSONSchemaBuilder.is_event_based({"event_based": True}) is True
    assert JSONSchemaBuilder.is_event_based({"event_based": False}) is False
    assert JSONSchemaBuilder.is_event_based({}) is False
    assert JSONSchemaBuilder.is_event_based(None) is False


def test_event_instruction_only_for_event_mode():
    assert JSONSchemaBuilder.event_instruction({"event_based": True})
    assert JSONSchemaBuilder.event_instruction({"sentiment": {}}) == ""


def test_event_instruction_carries_the_preset_limit():
    """`max_events_per_analysis` sits at the top of the scope in the presets."""
    text = JSONSchemaBuilder.event_instruction({"event_based": True, "max_events_per_analysis": 25})
    assert "25" in text


def test_event_instruction_defaults_when_unset():
    default = str(JSONSchemaBuilder.DEFAULT_LIMITS["max_events_per_analysis"])
    assert default in JSONSchemaBuilder.event_instruction({"event_based": True})


def test_build_json_instruction_appends_the_event_line():
    text = JSONSchemaBuilder.build_json_instruction(["sentiment"], {"event_based": True})
    assert "Режим анализа: по событиям" in text
    assert "sentiment_score" in text


def test_instruction_keeps_the_common_fields_even_with_no_types():
    """An empty list still gets the common fields — that is the point of them,
    and it is how this has always behaved, so the instruction is not empty."""
    text = JSONSchemaBuilder.build_json_instruction([])
    assert "analysis_title" in text
    assert "ВАЖНО" in text


# --- typed schema and the stored output_schema ------------------------------


def test_build_json_schema_types_every_field():
    schema = JSONSchemaBuilder.build_json_schema(["toxicity"])
    assert schema["type"] == "object"
    props = schema["properties"]
    assert props["toxicity_score"]["type"] == "number"
    assert props["toxicity_level"]["type"] == "string"
    assert "summary" in props


def test_build_json_schema_requires_what_it_describes():
    schema = JSONSchemaBuilder.build_json_schema(["intent"])
    assert set(schema["required"]) == {"summary", "intent_type", "confidence"}


def test_build_json_schema_resolves_placeholders():
    schema = JSONSchemaBuilder.build_json_schema(["intent"], {"intent": {"types": ["purchase"]}})
    assert schema["properties"]["intent_type"]["description"] == 'одно из: "purchase"'


def test_build_output_schema_delegates_to_the_same_contract():
    """`build_output_schema` is what the analyzer stores on the scenario; it must
    not describe a different shape than the prompt asks for."""
    assert build_output_schema(["toxicity"]) == JSONSchemaBuilder.build_json_schema(["toxicity"])


def test_build_output_schema_resolves_the_scenario_scope():
    stored = build_output_schema(["toxicity"], {"toxicity": {"toxicity_levels": ["low", "critical"]}})
    assert "critical" in stored["properties"]["toxicity_level"]["description"]


def test_output_schema_is_serialisable():
    """It is stored in a JSONB column, so it must survive a round trip."""
    schema = build_output_schema(["hashtag_analysis"])
    assert json.loads(json.dumps(schema, ensure_ascii=False)) == schema


def test_empty_analysis_types_still_yield_a_valid_schema():
    schema = build_output_schema([])
    assert schema["properties"]["summary"]["type"] == "string"
    assert schema["required"] == ["summary"]


# --- the rendered block -----------------------------------------------------


def test_formatted_block_is_well_formed_enough_to_read():
    """The instruction block is `{` ... `}` with one field per line."""
    text = JSONSchemaBuilder.format_schema_as_json(JSONSchemaBuilder.build_schema(["sentiment"]))
    assert text.startswith("{") and text.endswith("}")
    assert '"sentiment_score"' in text


def test_quotes_inside_a_description_do_not_break_the_block():
    """`hashtag_analysis` shows a JSON object in its description; an unescaped
    quote would end the value early for anything that parses the block."""
    text = JSONSchemaBuilder.format_schema_as_json(JSONSchemaBuilder.build_schema(["hashtag_analysis"]))
    assert text.count('"hashtags": "') == 1
    body = text.split('"hashtags": "', 1)[1].rsplit('"', 1)[0]
    assert '"' not in body
