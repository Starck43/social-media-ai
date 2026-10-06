"""
Scenario presets — ready-made analysis lenses the chat agent can offer.

A preset is a *starting point*, not a saved scenario: it carries the same
fields a scenario stores (what to collect, which metrics, how to prompt), so
`scenario_create` from a preset is a plain insert of expanded fields and the
rest of the pipeline cannot tell the difference. Every preset is expanded
through the same `ScenarioBuilder` the web wizard uses, which is what keeps a
chat-created scenario and a wizard-created one identical.

Presets are keyed by a stable slug (`brand_monitoring`) because that is what
the model puts into `scenario_create(template_key=...)`; the Russian `name` is
what a person reads in the chat.

See `docs/CHAT_BOT_SCENARIOS.md#scenario-templates-presets`.
"""

from __future__ import annotations

from typing import Any, Optional

from app.types import AnalysisType, ContentType
from app.types.enums.bot_types import AnalyzeType

# Fields a preset may carry. Anything outside this set is rejected, so a typo in
# a preset fails loudly here instead of writing a column the wizard never reads.
PRESET_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "content_types",
    "analysis_types",
    "analyze_type",
    "scope",
    "base_prompt",
    "media_overrides",
    "summary_prompt",
)

TEMPLATES: dict[str, dict[str, Any]] = {
    "brand_monitoring": {
        "name": "Мониторинг бренда",
        "description": "Упоминания бренда, их тональность и ключевые слова вокруг них.",
        "content_types": ["posts", "comments", "mentions"],
        "analysis_types": ["brand_mentions", "sentiment", "keywords"],
        "analyze_type": "themes",
        "scope": {"keywords": {"max_keywords": 10}, "relevance_filter": True, "min_confidence": 0.6},
        "base_prompt": (
            "Ты — бренд-аналитик. Найди упоминания бренда в контенте, "
            "определи тональность каждого упоминания и выдели ключевые слова, "
            "с которыми бренд чаще всего появляется. Период анализа: {date_range}."
        ),
    },
    "competitor_watch": {
        "name": "Мониторинг конкурентов",
        "description": "Активность конкурентов и реакция аудитории на неё, по дням.",
        "content_types": ["posts", "comments"],
        "analysis_types": ["competitor", "sentiment", "trends"],
        "analyze_type": "days",
        "scope": {},
        "base_prompt": (
            "Ты — аналитик конкурентной разведки. Сравни активность конкурентов "
            "из списка в параметрах анализа: их новые продукты, кампании и реакцию "
            "аудитории. Период анализа: {date_range}. Покажи, кто из конкурентов "
            "активнее и почему."
        ),
    },
    "customer_support": {
        "name": "Контроль поддержки",
        "description": "Обращения и жалобы клиентов: тональность и темы проблем.",
        "content_types": ["posts", "comments", "mentions"],
        "analysis_types": ["sentiment", "keywords", "intent"],
        "analyze_type": "themes",
        "scope": {"relevance_filter": True, "min_confidence": 0.6},
        "base_prompt": (
            "Ты — аналитик службы поддержки. Собери обращения и жалобы клиентов, "
            "определи их тональность и тему обращения, отдели реальные проблемы "
            "от обычных обсуждений. Период анализа: {date_range}."
        ),
    },
    "trend_spotter": {
        "name": "Поиск трендов",
        "description": "Что обсуждают и какие темы набирают обороты, по дням.",
        "content_types": ["posts", "comments"],
        "analysis_types": ["trends", "keywords", "topics"],
        "analyze_type": "days",
        "scope": {},
        "base_prompt": (
            "Ты — тренд-аналитик. Определи, какие темы набирают обороты в "
            "контенте за период {date_range}, и опиши их рост. Отдельно отметь "
            "темы, которые только появились."
        ),
    },
    "toxicity_guard": {
        "name": "Контроль токсичности",
        "description": "Резкий и оскорбительный контент в комментариях и постах.",
        "content_types": ["posts", "comments"],
        "analysis_types": ["toxicity", "sentiment"],
        "analyze_type": "themes",
        "scope": {},
        "base_prompt": (
            "Ты — модератор контента. Найди резкие, оскорбительные и "
            "провокационные высказывания, оцени их токсичность и объясни, "
            "почему сообщение попало в этот список. Период анализа: {date_range}."
        ),
    },
}


def template_keys() -> list[str]:
    """Preset slugs, in declaration order."""
    return list(TEMPLATES)


def get_template(key: str) -> Optional[dict[str, Any]]:
    """One preset by slug, or None — callers turn that into their own error."""
    preset = TEMPLATES.get((key or "").strip().lower())
    return dict(preset) if preset else None


def list_templates() -> list[dict[str, Any]]:
    """Presets as the chat sees them: slug, name, description and what they ask for.

    The `base_prompt` is deliberately left out — the tool result goes to the
    model, and five full prompts would crowd out the conversation it has to
    reason about.
    """
    return [
        {
            "key": key,
            "name": preset["name"],
            "description": preset["description"],
            "content_types": list(preset["content_types"]),
            "analysis_types": list(preset["analysis_types"]),
            "analyze_type": preset["analyze_type"],
        }
        for key, preset in TEMPLATES.items()
    ]


def _allowed(enum_cls: Any) -> str:
    """The db_values of an enum, as a comma-separated string for an error.

    `all_db_values()` is the shared helper the analysis/content enums expose;
    `AnalyzeType` predates it, so fall back to reading the members directly
    rather than adding a second spelling of the same list.
    """
    values = getattr(enum_cls, "all_db_values", None)
    return ", ".join(values() if callable(values) else [m.db_value for m in enum_cls])


def _enum_values(values: Any, enum_cls: Any, label: str) -> list[str]:
    """Validate a list of enum db_values, returning them unchanged when valid."""
    if not values:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label.capitalize()}: ожидается список, получено {type(values).__name__}")
    unknown = [v for v in values if enum_cls.get_by_value(v) is None]
    if unknown:
        raise ValueError(f"Неизвестный {label}: {', '.join(map(str, unknown))}. Доступные: {_allowed(enum_cls)}")
    return list(values)


def _enum_value(value: Any, enum_cls: Any, label: str) -> Optional[str]:
    """Validate a single enum db_value, or None when absent."""
    if value in (None, ""):
        return None
    if enum_cls.get_by_value(value) is None:
        raise ValueError(f"Неизвестный {label}: {value}. Доступные: {_allowed(enum_cls)}")
    return value


def expand_template(key: str, overrides: Optional[dict[str, Any]] = None) -> "ScenarioDraft":
    """A ready-to-save `ScenarioDraft` from a preset plus the caller's changes.

    Overrides win over the preset field by field; `scope` is merged per analysis
    type rather than replaced, so narrowing one analysis type's parameters does
    not drop the parameters the preset chose for the others. Enum values are
    validated against the production enums, so a bad value from the model is an
    error it can correct — not a row that fails at analysis time.
    """
    from app.services.ai.scenario_builder import ScenarioBuilder, ScenarioDraft

    preset = get_template(key)
    if preset is None:
        raise ValueError(f"Неизвестный шаблон: {key}. Доступные: {', '.join(template_keys())}")

    overrides = overrides or {}
    unknown = sorted(set(overrides) - set(PRESET_FIELDS))
    if unknown:
        raise ValueError(f"Неизвестные поля сценария: {', '.join(unknown)}")

    content_types = _enum_values(overrides.get("content_types"), ContentType, "тип контента")
    analysis_types = _enum_values(overrides.get("analysis_types"), AnalysisType, "тип анализа")
    analyze_type = _enum_value(overrides.get("analyze_type"), AnalyzeType, "режим анализа")

    merged: dict[str, Any] = {
        "name": preset["name"],
        "description": preset["description"],
        "content_types": content_types or list(preset["content_types"]),
        "analysis_types": analysis_types or list(preset["analysis_types"]),
        "analyze_type": analyze_type or preset["analyze_type"],
        "base_prompt": preset["base_prompt"],
        "summary_prompt": preset.get("summary_prompt"),
        "media_overrides": dict(preset.get("media_overrides") or {}),
        "scope": {},
    }

    # The defaults cover the analysis types the preset leaves to them; the
    # preset's own parameters then refine the ones it names.
    scope = ScenarioBuilder.build_scope_template(merged["analysis_types"])
    for analysis_type in merged["analysis_types"]:
        preset_config = (preset.get("scope") or {}).get(analysis_type)
        if preset_config:
            scope[analysis_type] = {**scope.get(analysis_type, {}), **preset_config}

    # Scenario-level (top-level) scope keys — relevance_filter, min_confidence —
    # are not tied to one analysis type; carry them through alongside the
    # per-type configs.
    for key, value in (preset.get("scope") or {}).items():
        if key not in merged["analysis_types"]:
            scope[key] = value

    for field, value in overrides.items():
        if field == "scope":
            for analysis_type, config in (value or {}).items():
                scope[analysis_type] = {**scope.get(analysis_type, {}), **config}
        elif field == "media_overrides":
            merged["media_overrides"] = {**merged["media_overrides"], **(value or {})}
        elif value is not None:
            merged[field] = value

    return ScenarioDraft(
        name=(merged["name"] or preset["name"]).strip()[:255],
        is_active=True,
        content_types=merged["content_types"],
        analysis_types=merged["analysis_types"],
        analyze_type=merged["analyze_type"],
        scope=ScenarioBuilder.sanitize_scope(scope, merged["analysis_types"]),
        base_prompt=merged["base_prompt"],
        media_overrides=merged["media_overrides"],
        summary_prompt=merged["summary_prompt"],
        description=merged["description"],
    )
