"""
JSON Schema Builder for dynamic LLM response schemas.

Two views of the same contract, and the second is the one the LLM sees:

- ``ANALYSIS_TYPE_SCHEMAS`` — the typed definition (a field's JSON type, its
  bounds, its enum values). It is the source of truth: add a field here and both
  the prompt text and the machine-readable schema follow.
- ``build_schema()`` — the flat ``{field: description}`` map the prompt has
  always rendered. Kept as-is in shape so every existing prompt and parser keeps
  working; the types are folded into the description text (``"число (number) от
  0.0 до 1.0"``), because a bare description list is what the models here answer.

Keys are ``AnalysisType.db_value`` — the strings a scenario stores in
``analysis_types``. An unknown key is skipped rather than guessed at, so a typo
in a scenario yields a thinner prompt instead of a wrong one.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# A field is (json_type, description). `json_type` is the type as it appears in
# the rendered instruction and as it appears in `build_json_schema()`.
Field = tuple[str, str]


class JSONSchemaBuilder:
    """Build dynamic JSON schemas for LLM prompts."""

    # Common fields that are ALWAYS included
    COMMON_FIELDS: Dict[str, str] = {
        "analysis_title": "строка, краткий заголовок анализа (3-7 слов), отражающий главную тему",
        "analysis_summary": (
            "строка, развернутое описание ситуации (2-4 предложения): что происходит, "
            "кого/что ищут, какие требования выделяют, какие паттерны видны"
        ),
    }

    # Typed response contract per analysis type, keyed by AnalysisType.db_value.
    # Descriptions carry their own placeholders ({max_topics}, {categories}, ...)
    # which build_schema() fills from the scenario's scope.
    ANALYSIS_TYPE_SCHEMAS: Dict[str, Dict[str, Field]] = {
        "sentiment": {
            "sentiment_score": ("number", "число от 0.0 (негатив) до 1.0 (позитив)"),
            "sentiment_label": ("string", "одно из: {categories}"),
        },
        "topics": {
            "main_topics": ("array", "список из {max_topics} главных тем"),
        },
        "keywords": {
            "keywords": ("array", "список из {max_keywords} ключевых слов"),
        },
        "engagement": {
            "likes": ("integer", "число лайков"),
            "comments": ("integer", "число комментариев"),
            "shares": ("integer", "число репостов и репостов друзей"),
            "engagement_rate": ("number", "число — вовлечённость от 0.0 до 1.0"),
        },
        "trends": {
            "trend_name": ("string", "название тренда"),
            "growth_rate": ("number", "число — темп роста; отрицательное значение означает спад"),
            "momentum": ("string", "одно из: {momentum_levels}"),
        },
        "toxicity": {
            "toxicity_score": ("number", "число от 0.0 (чистый) до 1.0 (токсичный)"),
            "toxicity_level": ("string", "одно из: {toxicity_levels}"),
        },
        "demographics": {
            "age_range": ("string", 'преобладающая возрастная группа, например "25-34"'),
            "gender_dist": (
                "object",
                'объект с распределением по полу в процентах, например {"female": 60, "male": 40}',
            ),
            "top_locations": ("array", "список до {max_locations} самых частых геолокаций"),
        },
        "viral_detection": {
            "viral_score": ("number", "число от 0.0 (не вирусный) до 1.0 (вирусный потенциал)"),
            "viral_factors": ("array", "список факторов, которые делают контент вирусным"),
            "predicted_reach": ("integer", "прогнозируемый охват в абсолютных единицах"),
        },
        "influencer": {
            "influencer_name": ("string", "имя или ник автора (с @ или без)"),
            "reach": ("integer", "охват автора в абсолютных единицах"),
            "impact_score": ("number", "число от 0.0 до 1.0 — влияние автора на обсуждение"),
        },
        "competitor": {
            "competitor": ("string", "название конкурента"),
            "activity_type": ("string", "одно из: {competitor_activities}"),
            "threat_level": ("string", "одно из: {threat_levels}"),
        },
        "intent": {
            "intent_type": ("string", "одно из: {intent_types}"),
            "confidence": ("number", "число от 0.0 до 1.0 — уверенность в намерении"),
        },
        "brand_mentions": {
            "brand": ("string", "название бренда, к которому относится упоминание"),
            "context": ("string", "краткий контекст, в котором встретилось упоминание"),
            "sentiment": ("number", "число от 0.0 (негатив) до 1.0 (позитив) — тональность упоминания"),
        },
        "hashtag_analysis": {
            "hashtags": (
                "array",
                'список хэштегов в формате [{"tag": "тег без #", "count": 5, "sentiment": 0.8}], '
                "не длиннее {max_hashtags} элементов",
            ),
        },
    }

    # Fields kept from the previous flat contract. Nothing in `AnalysisType`
    # selects them, but a scenario may still carry them in `analysis_types` and
    # its stored analyses were produced with them, so dropping them would
    # silently change what an existing scenario asks for.
    LEGACY_TYPE_FIELDS: Dict[str, Dict[str, Field]] = {
        "user_intent": {
            "primary_intent": (
                "string",
                'основное намерение пользователей: "информация", "жалоба", "вопрос", "обсуждение", "развлечение"',
            ),
            "secondary_intents": ("array", "список дополнительных намерений"),
            "call_to_action": ("boolean", "true/false — есть ли призыв к действию"),
        },
        "conversation_context": {
            "context_type": (
                "string",
                'тип контекста: "личное общение", "публичное обсуждение", '
                '"бизнес-коммуникация", "развлекательный контент"',
            ),
            "formality_level": ("number", "уровень формальности от 0.0 (неформальный) до 1.0 (формальный)"),
            "emotional_intensity": (
                "number",
                "эмоциональная интенсивность от 0.0 (нейтральный) до 1.0 (очень эмоциональный)",
            ),
        },
        "engagement_quality": {
            "discussion_depth": ("string", 'глубина обсуждения: "поверхностный", "средний", "глубокий"'),
            "audience_participation": ("string", 'уровень участия аультории: "низкий", "средний", "высокий"'),
            "content_value": ("string", 'ценность контента: "низкая", "средняя", "высокая"'),
        },
    }

    # Default vocabularies for the enum-valued fields, overridable per scenario
    # through scope[<analysis_type>][<key>].
    DEFAULT_VOCABULARIES: Dict[str, tuple[str, ...]] = {
        "categories": ("positive", "negative", "neutral"),
        "levels": ("high", "medium", "low"),
        "potential_levels": ("low", "medium", "high"),
        "sentiment_categories": ("positive", "negative", "neutral"),
        "toxicity_levels": ("low", "medium", "high"),
        "threat_levels": ("low", "medium", "high"),
        "momentum_levels": ("rising", "stable", "falling"),
        "competitor_activities": ("launch", "promotion", "partnership", "criticism", "other"),
        "intent_types": ("purchase", "complaint", "inquiry", "recommendation", "other"),
    }

    # Fallbacks for the list-length placeholders.
    DEFAULT_LIMITS: Dict[str, int] = {
        "max_topics": 5,
        "max_keywords": 15,
        "max_locations": 5,
        "max_hashtags": 10,
        "max_events_per_analysis": 50,
    }

    # Placeholder -> (key inside the type's scope config, scope key it borrows
    # from when set). `None` means the type's own config; a string means that
    # other type's config is read instead, which is how brand_mentions took its
    # vocabulary from sentiment.
    PLACEHOLDER_SCOPE_PATHS: Dict[str, tuple[str, str | None]] = {
        "categories": ("categories", None),
        "levels": ("levels", None),
        "potential_levels": ("potential_levels", None),
        "sentiment_categories": ("categories", "sentiment"),
        "toxicity_levels": ("toxicity_levels", None),
        "threat_levels": ("threat_levels", None),
        "momentum_levels": ("momentum_levels", None),
        "competitor_activities": ("activities", None),
        "intent_types": ("types", None),
    }

    @classmethod
    def fields_for(cls, analysis_type: str) -> Dict[str, Field]:
        """Typed fields of one analysis type (empty when it has no schema)."""
        return cls.ANALYSIS_TYPE_SCHEMAS.get(analysis_type) or cls.LEGACY_TYPE_FIELDS.get(analysis_type) or {}

    @classmethod
    def known_types(cls) -> set[str]:
        """Every analysis type this builder can describe."""
        return set(cls.ANALYSIS_TYPE_SCHEMAS) | set(cls.LEGACY_TYPE_FIELDS)

    @classmethod
    def validate_coverage(cls) -> set[str]:
        """Return db_values that have no schema entry (empty if all covered)."""
        from app.types.enums.analysis_types import AnalysisType

        known = set(cls.ANALYSIS_TYPE_SCHEMAS) | set(cls.LEGACY_TYPE_FIELDS)
        all_types = set(AnalysisType.all_db_values())
        return all_types - known

    @classmethod
    def _vocabulary(cls, placeholder: str, scope: Dict[str, Any], analysis_type: str) -> str:
        """Render one enum placeholder as a quoted, comma-separated list."""
        override_key, borrow_from = cls.PLACEHOLDER_SCOPE_PATHS.get(placeholder, (placeholder, None))

        if borrow_from is not None:
            # e.g. brand_mentions taking its categories from sentiment
            config = scope.get(borrow_from, {}) or {}
        else:
            config = scope.get(analysis_type, {}) or {}

        values = config.get(override_key)
        if not values:
            values = scope.get(placeholder)
        if not values:
            values = cls.DEFAULT_VOCABULARIES.get(placeholder)
        if not values:
            return '"значение"'
        return ", ".join(f'"{v}"' for v in values)

    @classmethod
    def _limit(cls, placeholder: str, scope: Dict[str, Any], analysis_type: str) -> str:
        """Render one numeric placeholder (a list length) from the scenario scope.

        Read at the top of the scope as well as under the type's own config: the
        presets put `max_events_per_analysis` at the top level, while
        `max_topics` lives under `scope["topics"]`. Reading only the nested key
        is why an event scope's own limit was silently replaced by the default.
        """
        config = scope.get(analysis_type, {}) or {} if analysis_type else {}
        default = cls.DEFAULT_LIMITS.get(placeholder)
        value = config.get(placeholder, scope.get(placeholder, default))
        return str(default if value is None else value)

    @classmethod
    def _resolve(cls, text: str, scope: Dict[str, Any], analysis_type: str) -> str:
        """Fill every {placeholder} in one description."""

        def _sub(match: re.Match) -> str:
            placeholder = match.group(1)
            if placeholder in cls.DEFAULT_LIMITS:
                return cls._limit(placeholder, scope, analysis_type)
            return cls._vocabulary(placeholder, scope, analysis_type)

        return re.sub(r"\{(\w+)\}", _sub, text)

    @classmethod
    def build_schema(cls, analysis_types: List[str], scope: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """The flat {field: description} map the JSON instruction renders.

        Common fields always come first; a field two types both declare is
        described by the first one that asks for it, so the merged prompt never
        contradicts itself.
        """
        schema: Dict[str, str] = {}
        scope = scope or {}

        schema.update(cls.COMMON_FIELDS)

        for analysis_type in analysis_types or []:
            for field_name, (json_type, field_desc) in cls.fields_for(analysis_type).items():
                if field_name in schema:
                    continue
                schema[field_name] = f"{json_type}, {cls._resolve(field_desc, scope, analysis_type)}"

        return schema

    @classmethod
    def build_json_schema(cls, analysis_types: List[str], scope: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The same contract as a real JSON Schema.

        Where a typed contract is wanted rather than prompt prose:
        `build_output_schema()` stores this on `AgentScenario.output_schema`, and
        stage 3's aggregator keys off the same field names. Placeholders resolve
        to text inside the descriptions, so it is guidance for a model, not a
        validator to run a response through.
        """
        properties: Dict[str, Any] = {}
        scope = scope or {}

        for analysis_type in analysis_types or []:
            for field_name, (json_type, field_desc) in cls.fields_for(analysis_type).items():
                if field_name in properties:
                    continue
                properties[field_name] = {
                    "type": json_type,
                    "description": cls._resolve(field_desc, scope, analysis_type),
                }

        return {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "Краткое резюме анализа"}, **properties},
            "required": ["summary", *properties],
        }

    @classmethod
    def is_event_based(cls, scope: Optional[Dict[str, Any]]) -> bool:
        """Whether the scenario analyses discrete events rather than themes.

        `scope["event_based"]` is what the presets set (scenario_presets.py). In
        event mode the model sees a batch of dated items and is asked to describe
        what happened, so a theme-style instruction ("find the emerging topics")
        would answer a different question than the one the analysis was grouped
        for.
        """
        return bool((scope or {}).get("event_based"))

    @classmethod
    def event_instruction(cls, scope: Optional[Dict[str, Any]]) -> str:
        """Extra prompt line for an event_based scenario, "" when not one.

        `max_events_per_analysis` is read from the top level of the scope, which
        is where the presets put it (not under a per-type config).
        """
        if not cls.is_event_based(scope):
            return ""
        limit = cls._limit("max_events_per_analysis", scope or {}, "")
        return (
            "\nРежим анализа: по событиям. Во входных данных — набор датированных сообщений "
            "за период; описывай произошедшее и изменения, а не ищи темы по всему корпусу. "
            f"Учитывай не более {limit} событий."
        )

    @classmethod
    def format_schema_as_json(cls, schema: Dict[str, str]) -> str:
        if not schema:
            return "{}"

        lines = ["{"]
        items = list(schema.items())
        for i, (field_name, field_desc) in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            # The block is read by a model, not parsed as JSON, but quotes inside
            # a description would break it for anything that tries.
            escaped = field_desc.replace('"', "'")
            lines.append(f'\t"{field_name}": "{escaped}"{comma}')
        lines.append("}")
        return "\n".join(lines)

    @classmethod
    def build_json_instruction(cls, analysis_types: List[str], scope: Optional[Dict[str, Any]] = None) -> str:
        schema = cls.build_schema(analysis_types, scope)

        if not schema:
            return ""

        schema_json = cls.format_schema_as_json(schema)

        return f"""

ВАЖНО: Верни результат СТРОГО в JSON формате:
{schema_json}

Не добавляй текст до или после JSON.
{cls.event_instruction(scope)}"""
