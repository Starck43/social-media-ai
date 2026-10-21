"""Shared scenario builder (app/services/ai/scenario_builder.py).

The web wizard and the admin form both read this module, so these tests pin
the shared contract: scope template generation, variable validation and the
final-prompt preview that mirrors what the analyzer would send to the LLM.
"""

from __future__ import annotations

from app.services.ai.scenario_builder import ScenarioBuilder, ScenarioDraft, scenario_builder
from app.types import MediaType


def test_build_scope_template_covers_only_selected_types() -> None:
    scope = scenario_builder.build_scope_template(["sentiment", "keywords"])
    assert set(scope) == {"sentiment", "keywords"}
    assert "categories" in scope["sentiment"]
    assert "max_keywords" in scope["keywords"]


def test_build_scope_template_empty_for_nothing() -> None:
    assert scenario_builder.build_scope_template([]) == {}
    single = scenario_builder.build_scope_template(["sentiment"])
    assert set(single) == {"sentiment"}
    assert "categories" in single["sentiment"]


def test_available_variables_delegates_to_registry() -> None:
    variables = scenario_builder.available_variables(MediaType.TEXT)
    assert {"text", "platform", "source_type", "stats"}.issubset(variables)


def test_unknown_variables_flags_only_unknown() -> None:
    prompt = "Смотри {text} и {stats.total_posts}, а ещё {bogus_var}"
    assert scenario_builder.unknown_variables(prompt) == ["bogus_var"]


def test_unknown_variables_empty_for_clean_prompt() -> None:
    assert scenario_builder.unknown_variables("Только {text} и {platform}") == []


def test_final_prompt_with_custom_substitutes_and_appends_schema() -> None:
    draft = ScenarioDraft(
        name="t",
        content_types=["posts"],
        analysis_types=["sentiment", "keywords"],
        base_prompt="Тональность из {platform}: {text}",
    )
    prompt = scenario_builder.final_prompt(draft, MediaType.TEXT)
    assert "VK" in prompt
    assert "Пример поста" in prompt
    # JSON instruction: the common fields always come first.
    assert "analysis_title" in prompt
    assert "analysis_summary" in prompt
    assert "sentiment_score" in prompt
    assert "keywords" in prompt


def test_final_prompt_without_custom_uses_default() -> None:
    draft = ScenarioDraft(name="t", content_types=["posts"], analysis_types=["topics"])
    prompt = scenario_builder.final_prompt(draft, MediaType.TEXT)
    assert "Проанализируй контент" in prompt
    assert "main_topics" in prompt


def test_preview_blocks_split_common_and_type_fields() -> None:
    draft = ScenarioDraft(name="t", analysis_types=["sentiment"])
    blocks = scenario_builder.preview_blocks(draft)
    # COMMON_FIELDS are always present
    assert "analysis_title" in blocks["common_fields"]
    assert "analysis_summary" in blocks["common_fields"]
    # Type-specific fields for sentiment
    assert "sentiment_score" in blocks["type_fields"]
    assert "sentiment_label" in blocks["type_fields"]
    # Additional fields may be present (topic_hint, confidence, is_meaningful)
    assert "text" in blocks["media_previews"]
    assert blocks["custom_prompt"] is None
    assert blocks["has_custom"] is False


def test_draft_scenario_round_trip() -> None:
    from app.models import AgentScenario

    scenario = AgentScenario(
        name="stored",
        content_types=["posts", "videos"],
        analysis_types=["toxicity"],
        scope={"toxicity": {"threshold": 0.8}},
        base_prompt="custom",
        media_overrides={"image": "over"},
        summary_prompt="summary",
        llm_strategy="cost_efficient",
        max_tokens=256,
    )
    draft = scenario_builder.draft_from_scenario(scenario)
    assert draft.name == "stored"
    assert draft.content_types == ["posts", "videos"]
    assert draft.scope == {"toxicity": {"threshold": 0.8}}
    assert draft.llm_strategy == "cost_efficient"

    other = AgentScenario(name="blank")
    scenario_builder.apply_draft(other, draft)
    assert other.name == "stored"
    assert other.base_prompt == "custom"
    assert other.media_overrides == {"image": "over"}
    assert other.summary_prompt == "summary"
    assert other.llm_strategy == "cost_efficient"
    assert other.max_tokens == 256


def test_media_types_for_maps_content_to_media() -> None:
    assert ScenarioBuilder.media_types_for(["posts", "comments"]) == ["text"]
    assert ScenarioBuilder.media_types_for(["videos", "reels"]) == ["video"]
    assert ScenarioBuilder.media_types_for(["posts", "videos"]) == ["text", "video"]


def test_transient_scenario_has_no_id() -> None:
    draft = ScenarioDraft(name="t")
    scenario = scenario_builder.transient_scenario(draft)
    assert scenario.id is None
