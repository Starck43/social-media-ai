"""Prompt system on the base_prompt + media_overrides + summary_prompt model.

Phase 1 of docs/CHAT_BOT_SCENARIOS.md: variable validation
(`validate_prompt` over `AVAILABLE_VARIABLES`) and prompt assembly
(`PromptBuilder.get_prompt` with base_prompt + media_overrides + JSON
detection). The tests pin the documented contract, not the old five-prompt
model.
"""

from __future__ import annotations

from app.models import AgentScenario
from app.services.ai.prompt_variables import PromptVariables, PromptSubstitution
from app.services.ai.prompts import PromptBuilder
from app.services.ai.scenario_builder import ScenarioBuilder, ScenarioDraft
from app.types import MediaType

# ── variable registry & validation ─────────────────────────────────────────


def test_validate_prompt_accepts_known_and_nested() -> None:
    assert PromptVariables.validate_prompt("Смотри {text} и {stats.total_posts}") == []
    assert PromptVariables.validate_prompt("") == []


def test_validate_prompt_flags_unknown() -> None:
    assert PromptVariables.validate_prompt("Ещё {bogus_var}") == ["bogus_var"]


def test_validate_prompt_accepts_documented_variables() -> None:
    prompt = "Период {date_range}, источник {source_name}, сценарий {scenario_name}"
    assert PromptVariables.validate_prompt(prompt) == []


def test_validate_prompt_accepts_scope_derived_names() -> None:
    prompt = "{max_keywords} {max_topics} {sentiment_categories}"
    assert PromptVariables.validate_prompt(prompt) == []


def test_available_variables_is_the_merged_registry() -> None:
    registry = PromptVariables.AVAILABLE_VARIABLES
    for name in (
        "text",
        "platform",
        "count",
        "text_analysis",
        "date_range",
        "source_name",
        "scenario_name",
        "trigger_condition",
    ):
        assert name in registry, f"{name} must be in AVAILABLE_VARIABLES"


def test_builder_unknown_variables_delegates_to_validate_prompt() -> None:
    assert ScenarioBuilder.unknown_variables("Только {text} и {bogus_var}") == ["bogus_var"]
    assert ScenarioBuilder.unknown_variables("Только {text}") == []


# ── prompt assembly ────────────────────────────────────────────────────────


def test_get_prompt_uses_base_prompt_and_substitutes() -> None:
    scenario = AgentScenario(base_prompt="Анализ из {platform}: {text}", analysis_types=["sentiment"])
    prompt = PromptBuilder.get_prompt(
        MediaType.TEXT, scenario=scenario, platform_name="VK", text="пост", stats={}, source_type=""
    )
    assert "Анализ из VK: пост" in prompt
    assert "analysis_title" in prompt, "COMMON_FIELDS must auto-append"


def test_get_prompt_injects_media_overrides() -> None:
    scenario = AgentScenario(
        base_prompt="Базовый промпт",
        media_overrides={"image": "Смотри {count} картинок из {platform}"},
        analysis_types=["sentiment"],
    )
    text_prompt = PromptBuilder.get_prompt(
        MediaType.TEXT, scenario=scenario, platform_name="VK", text="t", stats={}, source_type=""
    )
    assert "Базовый промпт" in text_prompt

    image_prompt = PromptBuilder.get_prompt(MediaType.IMAGE, scenario=scenario, platform_name="VK", count=4)
    assert "Смотри 4 картинок из VK" in image_prompt


def test_get_prompt_skips_schema_when_user_wrote_json() -> None:
    scenario = AgentScenario(base_prompt='Верни {"поле": "значение"}', analysis_types=["sentiment"])
    prompt = PromptBuilder.get_prompt(
        MediaType.TEXT, scenario=scenario, platform_name="VK", text="t", stats={}, source_type=""
    )
    assert "analysis_title" not in prompt
    assert "analysis_title" not in prompt or prompt.count("analysis_title") == 0


def test_get_unified_summary_prompt_uses_summary_prompt() -> None:
    scenario = AgentScenario(summary_prompt="Резюме: {text_analysis} и {image_analysis}")
    prompt = PromptBuilder.get_unified_summary_prompt({"x": 1}, {"y": 2}, {"z": 3}, scenario=scenario)
    assert "Резюме" in prompt
    assert "text_analysis" not in prompt, "the placeholder must be substituted"


def test_final_prompt_preview_uses_overrides() -> None:
    draft = ScenarioDraft(
        name="t",
        content_types=["posts"],
        analysis_types=["sentiment"],
        base_prompt="Базовый {platform}",
        media_overrides={"image": "Картинки {count}"},
    )
    text = ScenarioBuilder.final_prompt(draft, MediaType.TEXT)
    assert "Базовый VK" in text
    image = ScenarioBuilder.final_prompt(draft, MediaType.IMAGE)
    assert "Картинки 4" in image


def test_scope_derived_variables_substitute_from_scope() -> None:
    scenario = AgentScenario(
        base_prompt="Дай до {max_keywords} ключевых слов, категории: {sentiment_categories}",
        scope={
            "keywords": {"max_keywords": 12},
            "sentiment": {"categories": ["Позитив", "Негатив"]},
        },
    )
    prompt = PromptBuilder.get_prompt(
        MediaType.TEXT, scenario=scenario, platform_name="VK", text="t", stats={}, source_type=""
    )
    assert "{max_keywords}" not in prompt, "the scope-derived variable must resolve"
    assert "12" in prompt
    assert "Позитив, Негатив" in prompt
