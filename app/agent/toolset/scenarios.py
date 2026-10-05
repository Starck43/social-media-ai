"""Tools: read and change the scenarios of the current workspace.

The chat surface for `AgentScenario` — the analysis lens a task borrows. Read
tools answer immediately; the four write tools are `confirm=True`, so the
runtime stages them in `session.state['pending_confirmation']` and only executes
them after the owner answers «да».

Everything goes through the same `ScenarioBuilder`/enums the web wizard uses, so
a scenario created from a chat and one created from a wizard are the same row.
"""

from __future__ import annotations

from typing import Any, Optional

from app.agent.tools import tool

# Fields the owner may set through `scenario_update` / `scenario_clone`. Scoped
# to the scenario's own columns so a tool argument can never reach `id`,
# `tenant_id` or the timestamps.
WRITABLE_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "content_types",
    "analysis_types",
    "analyze_type",
    "scope",
    "base_prompt",
    "media_overrides",
    "summary_prompt",
    "is_active",
    "is_default",
    "max_tokens",
)


def _brief(scenario: Any) -> dict[str, Any]:
    """The short form both `scenario_list` and every write tool answer with."""
    return {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description or "",
        "is_active": scenario.is_active,
        "is_default": scenario.is_default,
        "analysis_types": list(scenario.analysis_types or []),
        "content_types": list(scenario.content_types or []),
        "analyze_type": scenario.analyze_type.db_value if scenario.analyze_type else None,
    }


def _full(scenario: Any) -> dict[str, Any]:
    """Everything a stored scenario carries, prompts included."""
    return {
        **_brief(scenario),
        "scope": scenario.scope or {},
        "base_prompt": scenario.base_prompt or "",
        "media_overrides": dict(scenario.media_overrides or {}),
        "summary_prompt": scenario.summary_prompt or "",
        "max_tokens": scenario.max_tokens,
    }


def _validate(changes: dict[str, Any]) -> dict[str, Any]:
    """Check enum values in a change set, raising on anything unknown.

    The model gets the error back and can correct itself, which is far cheaper
    than a scenario that fails silently at analysis time.
    """
    from app.services.ai.scenario_templates import _enum_value, _enum_values
    from app.types import AnalysisType, ContentType
    from app.types.enums.bot_types import AnalyzeType

    checked = dict(changes)
    for field, enum_cls, label in (
        ("content_types", ContentType, "тип контента"),
        ("analysis_types", AnalysisType, "тип анализа"),
    ):
        if field in checked:
            checked[field] = _enum_values(checked[field], enum_cls, label)
    if "analyze_type" in checked:
        checked["analyze_type"] = _enum_value(checked["analyze_type"], AnalyzeType, "режим анализа")
    return checked


def _reject_targets_in_scope(scope: dict[str, Any]) -> None:
    """Raise when a scenario's scope carries specific targets instead of methodology.

    Methodology (categories, limits, scales) lives in `AgentScenario.scope`; the
    specific objects to look for (brands, competitors, hashtags, influencers,
    keywords, topics) live on the task's `payload`. A scope that stores targets
    turns a reusable scenario into a one-brand template — reject it with guidance
    instead of silently building that.
    """
    from app.services.ai.param_registry import target_params_in_scope

    found = target_params_in_scope(scope)
    if found:
        raise ValueError(
            "Конкретные цели анализа хранятся в задаче (payload: brands, competitors, "
            "hashtags, influencer_names, keywords_list, topic_list), а не в сценарии — сценарий "
            "это методика. Укажите цели при создании задачи (task_add). Найдено в scope: " + ", ".join(found)
        )


def _unknown_variables(draft: Any) -> list[str]:
    """Placeholders in a draft's prompts that no variable will fill.

    A warning, never an error: the same rule the web wizard and the REST layer
    apply on save.
    """
    from app.services.ai.scenario_builder import ScenarioBuilder

    unknown: list[str] = []
    for prompt in (draft.base_prompt, draft.summary_prompt, *(draft.media_overrides or {}).values()):
        for name in ScenarioBuilder.unknown_variables(prompt or ""):
            if name not in unknown:
                unknown.append(name)
    return unknown


async def _save_preferences(draft: Any) -> dict[str, Any]:
    """Remember the choices this scenario was created with.

    Only what a person could plausibly want again: the grouping mode and the
    competitor/brand list. Called after a successful write, so a rejected
    scenario teaches nothing.
    """
    from app.services.ai import scenario_prefs

    prefs: dict[str, Any] = {}
    if draft.analyze_type:
        prefs["preferred_analyze_type"] = draft.analyze_type
    competitor = (draft.scope or {}).get("competitor") or {}
    brands = competitor.get("brand_names") or competitor.get("competitor_list") or []
    if brands:
        prefs["brands"] = [str(b) for b in brands]
    return await scenario_prefs.remember(**prefs) if prefs else {}


def _draft_from_fields(fields: dict[str, Any]) -> Any:
    """Build a draft from explicit fields, generating the scope from the types.

    Same rule as the wizard: nobody has to write JSON, so a scenario created
    from the chat arrives with a working scope.
    """
    from app.services.ai.scenario_builder import ScenarioBuilder, ScenarioDraft

    checked = _validate(fields)
    analysis_types = checked.get("analysis_types") or []
    scope = checked.get("scope") or ScenarioBuilder.build_scope_template(analysis_types)
    _reject_targets_in_scope(scope)
    return ScenarioDraft(
        name=(checked.get("name") or "").strip()[:255],
        description=checked.get("description"),
        is_active=True,
        content_types=checked.get("content_types") or [],
        analysis_types=analysis_types,
        analyze_type=checked.get("analyze_type"),
        scope=ScenarioBuilder.sanitize_scope(scope, analysis_types),
        base_prompt=checked.get("base_prompt"),
        media_overrides=dict(checked.get("media_overrides") or {}),
        summary_prompt=checked.get("summary_prompt"),
        max_tokens=checked.get("max_tokens"),
    )


@tool(
    name="scenario_list",
    description=(
        "Показать список сценариев бота: id, название, описание, активен ли, является ли дефолтным, "
        "типы анализа и контента, режим группировки."
    ),
    parameters={
        "type": "object",
        "properties": {
            "only_active": {"type": "boolean", "description": "Только активные (по умолчанию true)"},
        },
        "required": [],
    },
)
async def scenario_list(only_active: bool = True) -> list[dict[str, Any]]:
    from app.models import AgentScenario

    qs = AgentScenario.objects.filter(is_active=True) if only_active else AgentScenario.objects.all()
    rows = await qs.order_by(AgentScenario.is_default.desc(), AgentScenario.name)
    return [_brief(s) for s in rows]


@tool(
    name="scenario_get",
    description=(
        "Показать все поля сценария по id: типы контента и анализа, режим группировки, "
        "параметры анализа (scope) и тексты промптов."
    ),
    parameters={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "ID сценария"}},
        "required": ["id"],
    },
)
async def scenario_get(id: int) -> dict[str, Any]:
    from app.models import AgentScenario

    # Through the manager, so another workspace's scenario reads as "not found".
    scenario = await AgentScenario.objects.get(id=int(id))
    if scenario is None:
        return {"error": f"Сценарий {id} не найден"}
    return _full(scenario)


@tool(
    name="scenario_templates",
    description=(
        "Показать готовые шаблоны сценариев: ключ, название, описание, какие типы контента и анализа "
        "собирают. Возьми подходящий шаблон, если он есть, — не изобретай поля заново."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
async def scenario_templates() -> dict[str, Any]:
    from app.services.ai.scenario_templates import list_templates

    return {"templates": list_templates()}


@tool(
    name="scenario_suggest_prompt",
    description=(
        "Написать текст промпта анализа по описанию задачи владельца на его языке. "
        "Возвращает готовый base_prompt; если модель недоступна — подсказку, что написать вручную."
    ),
    parameters={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Задача своими словами: что анализировать и зачем"},
        },
        "required": ["description"],
    },
)
async def scenario_suggest_prompt(description: str) -> dict[str, Any]:
    from app.services.ai.scenario_builder import ScenarioBuilder
    from app.services.ai.scenario_prompt_suggest import suggest_base_prompt

    prompt = await suggest_base_prompt(description)
    if not prompt:
        return {
            "ok": False,
            "message": "Модель сейчас недоступна — предложи владельцу написать промпт вручную.",
        }
    return {"ok": True, "prompt": prompt, "unknown_variables": ScenarioBuilder.unknown_variables(prompt)}


@tool(
    name="scenario_validate_prompt",
    description=(
        "Проверить текст промпта анализа: вернуть неизвестные переменные вида {foo}, "
        "которые не подставятся при анализе."
    ),
    parameters={
        "type": "object",
        "properties": {"prompt": {"type": "string", "description": "Текст промпта для проверки"}},
        "required": ["prompt"],
    },
)
async def scenario_validate_prompt(prompt: str) -> dict[str, Any]:
    from app.services.ai.scenario_builder import ScenarioBuilder

    unknown = ScenarioBuilder.unknown_variables(prompt or "")
    return {
        "ok": not unknown,
        "unknown_variables": unknown,
        "available_variables": sorted(ScenarioBuilder.base_variables()),
    }


@tool(
    name="scenario_create",
    description=(
        "Создать сценарий анализа. Два пути: из шаблона (template_key из scenario_templates, любые "
        "поля можно переопределить) или с нуля (name + analysis_types + base_prompt). "
        "Требует подтверждения владельца — сначала покажи ему превью, что получится."
    ),
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Название сценария"},
            "template_key": {
                "type": "string",
                "description": "Ключ шаблона из scenario_templates; поля шаблона можно переопределить",
            },
            "description": {"type": "string", "description": "Что сценарий делает, одной фразой"},
            "content_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Что собирать: posts, comments, mentions, videos, stories, reels, reactions",
            },
            "analysis_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Какие метрики считать: sentiment, keywords, topics, toxicity, engagement, "
                    "demographics, competitor, brand_mentions, trends, viral_detection, influencer, "
                    "intent, hashtag_analysis"
                ),
            },
            "analyze_type": {
                "type": "string",
                "enum": ["themes", "days", "sources", "monitored_users"],
                "description": "Как группировать результат: по темам, по дням, по источникам",
            },
            "scope": {
                "type": "object",
                "description": "Параметры анализа по типам; если не заданы — генерируются по analysis_types",
            },
            "base_prompt": {"type": "string", "description": "Основной промпт анализа"},
            "summary_prompt": {"type": "string", "description": "Промпт итоговой сводки (необязательно)"},
            "media_overrides": {
                "type": "object",
                "description": 'Промпты для отдельных типов медиа: {"image": "...", "video": "..."}',
            },
        },
        "required": ["name"],
    },
)
async def scenario_create(
    name: str,
    template_key: Optional[str] = None,
    description: Optional[str] = None,
    content_types: Optional[list[str]] = None,
    analysis_types: Optional[list[str]] = None,
    analyze_type: Optional[str] = None,
    scope: Optional[dict[str, Any]] = None,
    base_prompt: Optional[str] = None,
    summary_prompt: Optional[str] = None,
    media_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from app.services.ai.scenario import PlanLimitError, scenario_service

    overrides: dict[str, Any] = {"name": name}
    for field, value in (
        ("description", description),
        ("content_types", content_types),
        ("analysis_types", analysis_types),
        ("analyze_type", analyze_type),
        ("scope", scope),
        ("base_prompt", base_prompt),
        ("summary_prompt", summary_prompt),
        ("media_overrides", media_overrides),
    ):
        if value is not None:
            overrides[field] = value

    try:
        if template_key:
            from app.services.ai.scenario_templates import expand_template

            draft = expand_template(template_key, overrides)
        else:
            draft = _draft_from_fields(overrides)
    except ValueError as e:
        return {"error": str(e)}

    if not draft.analysis_types:
        return {"error": "Нужно выбрать хотя бы один тип анализа"}
    if not (draft.base_prompt or "").strip():
        return {"error": "Нужен основной промпт анализа (base_prompt)"}

    try:
        scenario = await scenario_service.create_scenario(
            name=draft.name,
            description=draft.description,
            analysis_types=draft.analysis_types,
            content_types=draft.content_types,
            scope=draft.scope,
            analyze_type=draft.analyze_type,
            base_prompt=draft.base_prompt,
            media_overrides=draft.media_overrides,
            summary_prompt=draft.summary_prompt,
            max_tokens=draft.max_tokens,
        )
    except PlanLimitError as e:
        return {"error": str(e)}

    await _save_preferences(draft)

    return {"status": "created", "scenario": _full(scenario), "unknown_variables": _unknown_variables(draft)}


@tool(
    name="scenario_update",
    description=(
        "Изменить поля сценария по id — частичное обновление, перечисленные поля только заменяются. "
        "scope сливается с текущим по типам анализа. Требует подтверждения владельца."
    ),
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "ID сценария"},
            "changes": {
                "type": "object",
                "description": (
                    "Поля для изменения: name, description, content_types, analysis_types, analyze_type, "
                    "scope, base_prompt, summary_prompt, media_overrides, is_active, is_default, max_tokens"
                ),
            },
        },
        "required": ["id", "changes"],
    },
)
async def scenario_update(id: int, changes: dict[str, Any]) -> dict[str, Any]:
    from app.models import AgentScenario
    from app.services.ai.scenario_builder import ScenarioBuilder

    scenario = await AgentScenario.objects.get(id=int(id))
    if scenario is None:
        return {"error": f"Сценарий {id} не найден"}

    unknown = sorted(set(changes or {}) - set(WRITABLE_FIELDS))
    if unknown:
        return {"error": f"Нельзя изменить поля: {', '.join(unknown)}"}
    if not changes:
        return {"error": "Не передано ни одного поля для изменения"}

    try:
        checked = _validate(changes)
    except ValueError as e:
        return {"error": str(e)}

    draft = ScenarioBuilder.draft_from_scenario(scenario)
    if "scope" in checked:
        # Editing must not drop parameters the new set does not mention — the
        # same merge the wizard performs on save.
        merged = {**(scenario.scope or {})}
        for analysis_type, config in (checked.pop("scope") or {}).items():
            merged[analysis_type] = {**(merged.get(analysis_type) or {}), **config}
        try:
            _reject_targets_in_scope(merged)
        except ValueError as e:
            return {"error": str(e)}
        checked["scope"] = merged
    for field, value in checked.items():
        setattr(draft, field, value)
    draft.scope = ScenarioBuilder.sanitize_scope(draft.scope or {}, draft.analysis_types or [])

    unknown_vars = _unknown_variables(draft)

    if draft.is_default:
        await AgentScenario.objects.filter(tenant_id=scenario.tenant_id, id__ne=scenario.id).update(is_default=False)

    await AgentScenario.objects.update_by_id(
        scenario.id,
        name=draft.name,
        description=draft.description,
        content_types=draft.content_types,
        analysis_types=draft.analysis_types,
        analyze_type=draft.analyze_type,
        scope=draft.scope,
        base_prompt=draft.base_prompt,
        media_overrides=draft.media_overrides,
        summary_prompt=draft.summary_prompt,
        is_active=draft.is_active,
        is_default=draft.is_default,
        max_tokens=draft.max_tokens,
    )

    await _save_preferences(draft)
    stored = await AgentScenario.objects.get(id=scenario.id)
    return {"status": "updated", "scenario": _full(stored), "unknown_variables": unknown_vars}


@tool(
    name="scenario_clone",
    description=(
        "Создать копию сценария по id под новым именем, при желании изменив часть полей в копии. "
        "Оригинал не меняется. Требует подтверждения владельца."
    ),
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "source_id": {"type": "integer", "description": "ID сценария-оригинала"},
            "new_name": {"type": "string", "description": "Название копии"},
            "changes": {
                "type": "object",
                "description": "Поля, которые нужно изменить только в копии",
            },
        },
        "required": ["source_id", "new_name"],
    },
)
async def scenario_clone(source_id: int, new_name: str, changes: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    from app.models import AgentScenario
    from app.services.ai.scenario import PlanLimitError, scenario_service
    from app.services.ai.scenario_builder import ScenarioBuilder

    source = await AgentScenario.objects.get(id=int(source_id))
    if source is None:
        return {"error": f"Сценарий {source_id} не найден"}

    unknown = sorted(set(changes or {}) - set(WRITABLE_FIELDS))
    if unknown:
        return {"error": f"Нельзя изменить поля: {', '.join(unknown)}"}

    draft = ScenarioBuilder.draft_from_scenario(source)
    try:
        checked = _validate(changes or {})
    except ValueError as e:
        return {"error": str(e)}

    # A copy is a new row, so it cannot inherit the source's default flag —
    # two defaults in one workspace is a state the list page cannot show.
    for field, value in checked.items():
        if field == "is_default":
            continue
        setattr(draft, field, value)
    draft.name = new_name.strip()[:255]
    draft.is_default = False
    draft.scope = ScenarioBuilder.sanitize_scope(draft.scope or {}, draft.analysis_types or [])

    try:
        clone = await scenario_service.create_scenario(
            name=draft.name,
            description=draft.description,
            analysis_types=draft.analysis_types,
            content_types=draft.content_types,
            scope=draft.scope,
            analyze_type=draft.analyze_type,
            base_prompt=draft.base_prompt,
            media_overrides=draft.media_overrides,
            summary_prompt=draft.summary_prompt,
            max_tokens=draft.max_tokens,
        )
    except PlanLimitError as e:
        return {"error": str(e)}

    return {
        "status": "cloned",
        "source_id": source.id,
        "scenario": _full(clone),
        "unknown_variables": _unknown_variables(draft),
    }


@tool(
    name="scenario_delete",
    description=(
        "Удалить сценарий по id. Задачи, которые его использовали, продолжат работать без сценария "
        "(связь обнуляется) — предупреди владельца, если сценарий был привязан к задачам. "
        "Требует подтверждения."
    ),
    confirm=True,
    parameters={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "ID сценария"}},
        "required": ["id"],
    },
)
async def scenario_delete(id: int) -> dict[str, Any]:
    from app.models import AgentScenario, AgentTask

    scenario = await AgentScenario.objects.get(id=int(id))
    if scenario is None:
        return {"error": f"Сценарий {id} не найден"}

    # The FK is ON DELETE SET NULL, so the tasks survive but lose their lens.
    # Saying so is the difference between an expected and a surprising result.
    affected = await AgentTask.objects.filter(agent_scenario_id=scenario.id).count()
    deleted = await AgentScenario.objects.delete(id=scenario.id)
    if not deleted:
        return {"error": f"Не удалось удалить сценарий {id}"}
    return {
        "status": "deleted",
        "id": scenario.id,
        "name": scenario.name,
        "tasks_left_without_scenario": affected,
    }
