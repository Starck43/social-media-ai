"""
Scenario builder — the shared core for the web wizard and the admin form.

Everything the two UIs do with a scenario goes through this module, so the
web step-by-step wizard and the admin form produce the same scope template,
validate the same prompt variables and show the same final prompt. It has no
UI of its own: it returns plain data the views pass to their templates.

Pieces:
- `ScenarioDraft` — the wizard state, the same fields a scenario stores.
- `build_scope_template(analysis_types)` — the auto-generated scope JSON from
  `ANALYSIS_TYPE_DEFAULTS`, keyed by the selected analysis types.
- `available_variables(media_type)` / `unknown_variables(prompt)` — the prompt
  variable registry and its validation.
- `final_prompt(draft, media_type, sample)` — the prompt that actually goes to
  the LLM, built with the production `PromptBuilder` (custom + JSON instruction
  from `JSONSchemaBuilder`), variables substituted by the caller's sample.
- `preview_blocks(draft)` — the same prompt decomposed for a nice preview UI:
  base prompt, common fields, schema fields, sample variables.
- `draft_from_scenario(scenario)` / `apply_draft(scenario, draft)` — serialise
  between the model and the draft, so edit and create share the same shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.types import ContentType, MediaType
from app.types.enums.analysis_types import AnalysisType
from app.utils.enum_helpers import get_enum_value


@dataclass
class ScenarioDraft:
    """The wizard's state — every field a scenario can carry.

    Both the web wizard and the admin form build this, then `apply_draft`
    writes it. Missing analysis/content types default to empty, matching the
    model's columns.
    """

    name: str
    description: Optional[str] = None
    is_active: bool = True
    is_default: bool = False
    content_types: list[str] = field(default_factory=list)
    analysis_types: list[str] = field(default_factory=list)
    analyze_type: Optional[str] = None
    scope: dict[str, Any] = field(default_factory=dict)
    text_prompt: Optional[str] = None
    image_prompt: Optional[str] = None
    video_prompt: Optional[str] = None
    audio_prompt: Optional[str] = None
    unified_summary_prompt: Optional[str] = None
    llm_strategy: Optional[str] = None
    text_llm_model_id: Optional[int] = None
    image_llm_model_id: Optional[int] = None
    video_llm_model_id: Optional[int] = None
    max_tokens: Optional[int] = None

    @property
    def media_type(self) -> MediaType:
        """The primary media type, derived from content_types when set.

        The wizard's one base prompt is the text analysis prompt unless the
        user narrows the scenario to a single visual medium.
        """
        if self.content_types and set(self.content_types) <= {"videos", "reels"}:
            return MediaType.VIDEO
        if self.content_types and set(self.content_types) <= {"stories"}:
            return MediaType.IMAGE
        return MediaType.TEXT

    def for_media(self, media: MediaType) -> dict[str, Any]:
        """The runtime context a preview needs for one media type."""
        media_value = get_enum_value(media)
        base = {
            "platform_name": "VK",
            "source_type": "группа",
            "stats": {
                "total_posts": 12,
                "total_reactions": 34,
                "total_comments": 8,
                "avg_reactions_per_post": 2.8,
                "avg_comments_per_post": 0.7,
                "date_range": {"first": "01.10.2026", "last": "04.10.2026"},
            },
        }
        if media_value == "text":
            base["text"] = (
                "Пример поста №1: «Наш новый продукт уже в продаже — успейте по акции!»\n"
                "Пример поста №2: «Где мой заказ? Третий день без ответа, поддержка молчит»\n"
                "Пример поста №3: «Коллеги, кто уже попробовал новую версию? Делитесь мнением»"
            )
        else:
            base["count"] = 4
        return base


class ScenarioBuilder:
    """Pure helpers shared by the web wizard and the admin form."""

    #: The prompt fields a scenario carries, in wizard order — the base prompt
    #: is `text_prompt`, the rest are optional per-media overrides.
    PROMPT_FIELDS: tuple[str, ...] = (
        "text_prompt",
        "image_prompt",
        "video_prompt",
        "audio_prompt",
        "unified_summary_prompt",
    )

    @classmethod
    def build_scope_template(cls, analysis_types: list[str]) -> dict[str, Any]:
        """Auto-generate the scope JSON from the selected analysis types.

        Each selected type gets its default config (from
        `ANALYSIS_TYPE_DEFAULTS`), so a user who only ticks checkboxes still
        gets a working scope. The result is what `apply_draft` stores — no
        manual JSON needed in the web UI.
        """
        from app.core.analysis_constants import get_analysis_defaults

        scope: dict[str, Any] = {}
        for at in analysis_types or []:
            if at not in scope:
                defaults = get_analysis_defaults(at) or {}
                if defaults:
                    scope[at] = dict(defaults)
        return scope

    @classmethod
    def available_variables(cls, media_type: MediaType) -> dict[str, str]:
        """Variable name → description, the dropdown a prompt editor offers.

        Delegates to `PromptVariables`, the single registry the substitution
        code reads, so the offered list cannot drift from what actually
        substitutes at runtime.
        """
        from app.services.ai.prompt_variables import PromptVariables

        return dict(PromptVariables.get_variables_for_media_type(media_type))

    @classmethod
    def unknown_variables(cls, prompt: str) -> list[str]:
        """Placeholders in `prompt` that no known variable will fill.

        Only the *text* variable set is checked here — the media overrides use
        the same names (`count`, `platform`), so one set covers the wizard's
        base prompt. Scope-derived custom variables are resolved from the
        draft's scope at build time and cannot be validated statically.
        """
        from app.services.ai.prompt_variables import PromptVariables

        known = set(PromptVariables.TEXT_VARIABLES)
        found: list[str] = []
        for match in re.findall(r"\{([^}]+)\}", prompt or ""):
            # Nested paths ({stats.total_posts}) are legitimate; the check is
            # for the top-level name only.
            name = match.split(".")[0]
            if name not in known and name not in found:
                found.append(name)
        return found

    @classmethod
    def transient_scenario(cls, draft: ScenarioDraft):
        """An in-memory `AgentScenario` from the draft, no DB row.

        `PromptBuilder.get_prompt` reads `text_prompt`, `scope`,
        `analysis_types` and the model ids off the scenario, so a transient
        instance lets the preview reuse the exact production prompt path.
        """
        from app.models import AgentScenario

        scenario = AgentScenario(
            name=draft.name,
            description=draft.description,
            content_types=draft.content_types,
            analysis_types=draft.analysis_types,
            scope=draft.scope or {},
            text_prompt=draft.text_prompt,
            image_prompt=draft.image_prompt,
            video_prompt=draft.video_prompt,
            audio_prompt=draft.audio_prompt,
            unified_summary_prompt=draft.unified_summary_prompt,
            llm_strategy=draft.llm_strategy,
            text_llm_model_id=draft.text_llm_model_id,
            image_llm_model_id=draft.image_llm_model_id,
            video_llm_model_id=draft.video_llm_model_id,
            max_tokens=draft.max_tokens,
        )
        scenario.id = None
        return scenario

    @classmethod
    def sanitize_scope(cls, scope: dict[str, Any], analysis_types: list[str]) -> dict[str, Any]:
        """Drop per-type configs for analysis types no longer selected.

        The wizard auto-fills scope from the ticked checkboxes; when a user
        unticks one, its config would otherwise linger and describe output the
        scenario no longer asks for. Custom variables (not named after an
        `AnalysisType`) always stay.
        """
        selected = set(analysis_types or [])
        cleaned: dict[str, Any] = {}
        for key, value in (scope or {}).items():
            if key in AnalysisType.all_db_values() and key not in selected:
                continue
            cleaned[key] = value
        return cleaned

    @classmethod
    def final_prompt(cls, draft: ScenarioDraft, media_type: MediaType, sample: Optional[dict] = None) -> str:
        """The prompt that would actually go to the LLM for one media type.

        Built through `PromptBuilder`, the same entry point the analyzer calls,
        so the preview cannot drift from production: a custom prompt (with
        variables substituted) or the default one, plus the JSON instruction
        with COMMON_FIELDS and the schema for the draft's analysis types.
        """
        from app.services.ai.prompts import PromptBuilder

        scenario = cls.transient_scenario(draft)
        context = dict(sample or {})
        context.update(draft.for_media(media_type))
        return PromptBuilder.get_prompt(media_type, scenario=scenario, **context)

    @classmethod
    def preview_blocks(cls, draft: ScenarioDraft) -> dict[str, Any]:
        """The final prompt decomposed for the preview UI.

        Returns the full assembled prompt per media plus the pieces the
        instructions are made of — the common fields, the analysis-type fields
        and the sample variables — so a reader sees exactly what the system adds
        to their own prompt before it reaches the model.
        """
        from app.services.ai.json_schema_builder import JSONSchemaBuilder

        scenario = cls.transient_scenario(draft)
        schema = JSONSchemaBuilder.build_schema(draft.analysis_types or [], draft.scope or {})

        common = {k: v for k, v in schema.items() if k in JSONSchemaBuilder.COMMON_FIELDS}
        type_fields = {k: v for k, v in schema.items() if k not in JSONSchemaBuilder.COMMON_FIELDS}

        media_previews: dict[str, str] = {}
        for media in (MediaType.TEXT, MediaType.IMAGE, MediaType.VIDEO, MediaType.AUDIO):
            media_previews[get_enum_value(media)] = cls.final_prompt(draft, media)

        return {
            "common_fields": common,
            "type_fields": type_fields,
            "media_previews": media_previews,
            "custom_prompt": draft.text_prompt or None,
            "has_custom": bool(draft.text_prompt),
            "base_media": get_enum_value(draft.media_type),
        }

    @classmethod
    def draft_from_scenario(cls, scenario) -> ScenarioDraft:
        """Snapshot a stored scenario into a wizard draft (for edit mode)."""
        return ScenarioDraft(
            name=scenario.name,
            description=scenario.description,
            is_active=scenario.is_active,
            is_default=scenario.is_default,
            content_types=list(scenario.content_types or []),
            analysis_types=list(scenario.analysis_types or []),
            analyze_type=getattr(scenario.analyze_type, "db_value", None) if scenario.analyze_type else None,
            scope=dict(scenario.scope or {}),
            text_prompt=scenario.text_prompt,
            image_prompt=scenario.image_prompt,
            video_prompt=scenario.video_prompt,
            audio_prompt=scenario.audio_prompt,
            unified_summary_prompt=scenario.unified_summary_prompt,
            llm_strategy=scenario.llm_strategy if isinstance(scenario.llm_strategy, str) else (
                scenario.llm_strategy.value if scenario.llm_strategy else None
            ),
            text_llm_model_id=scenario.text_llm_model_id,
            image_llm_model_id=scenario.image_llm_model_id,
            video_llm_model_id=scenario.video_llm_model_id,
            max_tokens=scenario.max_tokens,
        )

    @classmethod
    def apply_draft(cls, scenario, draft: ScenarioDraft) -> None:
        """Write the draft onto a scenario row (create or edit)."""
        scenario.name = draft.name
        scenario.description = draft.description
        scenario.is_active = draft.is_active
        scenario.is_default = draft.is_default
        scenario.content_types = list(draft.content_types)
        scenario.analysis_types = list(draft.analysis_types)
        scenario.scope = dict(draft.scope or {})
        scenario.analyze_type = draft.analyze_type
        scenario.text_prompt = draft.text_prompt
        scenario.image_prompt = draft.image_prompt
        scenario.video_prompt = draft.video_prompt
        scenario.audio_prompt = draft.audio_prompt
        scenario.unified_summary_prompt = draft.unified_summary_prompt
        scenario.llm_strategy = draft.llm_strategy
        scenario.text_llm_model_id = draft.text_llm_model_id
        scenario.image_llm_model_id = draft.image_llm_model_id
        scenario.video_llm_model_id = draft.video_llm_model_id
        scenario.max_tokens = draft.max_tokens

    @classmethod
    def media_types_for(cls, content_types: list[str]) -> list[str]:
        """Distinct media types the given content types need (text/image/video)."""
        from app.types import ContentType

        media: set[str] = set()
        for ct in content_types or []:
            member = ContentType.get_by_value(ct)
            if member is not None:
                media.add(member.required_media_type)
        return sorted(media)


scenario_builder = ScenarioBuilder()
