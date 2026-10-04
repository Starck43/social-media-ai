"""Scenarios `/app/scenarios` — list and manage bot scenarios.

M3: list scenarios, set default, view details.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func

from app.models.agent_scenario import AgentScenario

from .deps import add_flash, ensure_csrf, guard_web, render

router = APIRouter(prefix="/scenarios")


@router.get("")
@router.get("/")
async def scenarios_list(request: Request):
    """Scenarios with their task counts (LEFT JOIN — a scenario may have none)."""
    # No `tenant_id` predicate: the manager's guard already scopes the rows.
    from app.models import AgentTask

    rows = await (
        AgentScenario.objects.outerjoin(AgentTask, AgentScenario.id == AgentTask.agent_scenario_id)
        .values(
            AgentScenario.id,
            AgentScenario.name,
            AgentScenario.description,
            AgentScenario.is_default,
            AgentScenario.is_active,
            AgentScenario.analysis_types,
            AgentScenario.content_types,
            func.count(AgentTask.id).label("task_count"),
        )
        .group_by(AgentScenario.id)
        .order_by(AgentScenario.is_default.desc(), AgentScenario.name)
        .rows()
    )

    scenarios = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description or "",
            "is_default": r.is_default,
            "is_active": r.is_active,
            "analysis_types": r.analysis_types or [],
            "content_types": r.content_types or [],
            "task_count": r.task_count,
        }
        for r in rows
    ]
    return render(request, "web/scenarios.html", section="scenarios", scenarios=scenarios)


@router.post("/set-default")
async def scenarios_set_default(
    request: Request,
    scenario_id: int = Form(...),
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/scenarios", status_code=302)

    denied = guard_web(request, "agentscenario", "update", back="/app/scenarios")
    if denied is not None:
        return denied

    target_scenario = await _own_scenario(request, scenario_id)

    if target_scenario is None:
        add_flash(request, "error", "Сценарий не найден")
        return RedirectResponse("/app/scenarios", status_code=302)

    if not target_scenario.is_active:
        add_flash(request, "error", "Нельзя сделать активным неактивный сценарий")
        return RedirectResponse("/app/scenarios", status_code=302)

    # Clear is_default on every other scenario, then set this one. Two bulk
    # statements instead of a hand-built select().update() on a private session.
    # The workspace predicate is explicit: under a tenant bypass (a superuser
    # browsing) an unscoped `filter(is_default=True)` would clear *every*
    # workspace's default scenario, not just this one's.
    tenant_id = request.state.tenant_id
    await AgentScenario.objects.filter(tenant_id=tenant_id, is_default=True).update(is_default=False)
    await AgentScenario.objects.filter(tenant_id=tenant_id, id=scenario_id).update(is_default=True)

    add_flash(request, "success", f"Сценарий '{target_scenario.name}' теперь используется по умолчанию")
    return RedirectResponse("/app/scenarios", status_code=302)


# ── editor ─────────────────────────────────────────────────────────────────


def _optional_int(raw: str) -> int | None:
    """An empty form box is None (column is nullable), never 0."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"«{raw}» — нужно целое число или пустое значение")
    if value < 0:
        raise ValueError("значение не может быть отрицательным")
    return value


def _json_object(raw: str, label: str) -> dict:
    """Parse a JSON object field, with the field name in the error message.

    The UI accepts raw JSON because these columns are JSON by design; a bare
    `json.JSONDecodeError` would surface as a 500 with no hint about which box
    was wrong.
    """
    import json

    raw = raw.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: некорректный JSON ({exc.msg}, строка {exc.lineno})")
    if not isinstance(parsed, dict):
        raise ValueError(f'{label}: нужен JSON-объект, например {{"keywords": ["встреча"]}}')
    return parsed


def _comma_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _pretty_json(value: object) -> str:
    """Pretty-print a JSON column for a textarea: `null` becomes `{}`."""
    import json

    return json.dumps(value or {}, ensure_ascii=False, indent=2)


async def _own_scenario(request: Request, scenario_id: int) -> "AgentScenario | None":
    """Fetch one scenario of the *current* workspace, or None.

    The `tenant_id` predicate is explicit rather than left to the manager's
    guard, because every other `/app` lookup does the same (see `sources.py`,
    `tasks.py`) and it stays fail-closed even when the surrounding code runs
    under a tenant bypass — a superuser browsing, a test harness. No context,
    no leak: a foreign scenario's prompts are unreachable.
    """
    from app.models import AgentScenario

    return await AgentScenario.objects.get(id=scenario_id, tenant_id=request.state.tenant_id)


@router.get("/{scenario_id}")
async def scenario_detail(request: Request, scenario_id: int):
    """Read + edit one scenario: prompts, scope, triggers, guards.

    The scenario is looked up through the manager, so a foreign id reads as
    "not found" rather than leaking another workspace's prompts.
    """
    scenario = await _own_scenario(request, scenario_id)
    if scenario is None:
        add_flash(request, "error", "Сценарий не найден")
        return RedirectResponse("/app/scenarios", status_code=302)

    from app.models.llm_model import LLMModel
    from app.types import AnalysisType, AgentActionType, BotTriggerType, ContentType, LLMStrategyType
    from app.types.enums.bot_types import AnalyzeType

    llm_models = [
        {"id": m.id, "name": m.name, "model_type": m.model_type}
        for m in await LLMModel.objects.filter(is_active=True).order_by(LLMModel.name)
    ]

    return render(
        request,
        "web/scenario_detail.html",
        section="scenarios",
        scenario=scenario,
        content_types=list(ContentType),
        analysis_types=list(AnalysisType),
        analyze_types=list(AnalyzeType),
        trigger_types=list(BotTriggerType),
        action_types=list(AgentActionType),
        llm_strategies=LLMStrategyType.choices(),
        llm_models=llm_models,
        pretty_json=_pretty_json,
    )


def _enum_by_value(enum_cls, raw: str):
    """Resolve a form value to its enum member, or None when unknown/blank.

    Scenario enum columns are stored by *name* (`trigger_type`,
    `action_type`), so writing the raw form string would produce a value the
    PostgreSQL enum rejects. An unrecognised value is refused here rather than
    surfacing as an IntegrityError on flush.

    Both shapes are accepted: `DatabaseEnum` exposes `get_by_value`, but
    `AnalyzeType` is a plain `Enum` (no `db_value`), so fall back to matching
    `.value` and then `.name` — otherwise selecting a mode would raise
    AttributeError instead of being validated.
    """
    if not raw:
        return None
    getter = getattr(enum_cls, "get_by_value", None)
    member = getter(raw) if getter is not None else None
    if member is None:
        member = next((m for m in enum_cls if m.value == raw), None)
    if member is None:
        member = enum_cls[raw] if raw in enum_cls.__members__ else None
    return member


async def _collect_fields(form: dict) -> dict:
    """Turn the editor form into a column→value dict, validating as it goes.

    Raises `ValueError` with a user-facing message: the caller flashes it and
    sends the editor back, so a bad JSON box never becomes a 500.
    """
    from app.types import AnalysisType, AgentActionType, BotTriggerType, ContentType, LLMStrategyType
    from app.types.enums.bot_types import AnalyzeType

    trigger_type = _enum_by_value(BotTriggerType, form.get("trigger_type", ""))
    if form.get("trigger_type") and trigger_type is None:
        raise ValueError(f"Неизвестный триггер: {form['trigger_type']}")

    action_type = _enum_by_value(AgentActionType, form.get("action_type", ""))
    if form.get("action_type") and action_type is None:
        raise ValueError(f"Неизвестное действие: {form['action_type']}")

    analyze_type = _enum_by_value(AnalyzeType, form.get("analyze_type", ""))
    if form.get("analyze_type") and analyze_type is None:
        raise ValueError(f"Неизвестный режим анализа: {form['analyze_type']}")

    llm_strategy = _enum_by_value(LLMStrategyType, form.get("llm_strategy", ""))
    if form.get("llm_strategy") and llm_strategy is None:
        raise ValueError(f"Неизвестная стратегия LLM: {form['llm_strategy']}")

    def _optional_model(raw_key: str) -> int | None:
        raw = (form.get(raw_key) or "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise ValueError("Выберите модель из списка")
        return int(raw)

    # Multi-selects arrive as repeated fields; an empty list means "all".
    content_types = form.getlist("content_types") if hasattr(form, "getlist") else form.get("content_types") or []
    analysis_types = form.getlist("analysis_types") if hasattr(form, "getlist") else form.get("analysis_types") or []
    for raw, enum_cls, label in (
        (content_types, ContentType, "тип контента"),
        (analysis_types, AnalysisType, "тип анализа"),
    ):
        unknown = [v for v in raw if _enum_by_value(enum_cls, v) is None]
        if unknown:
            raise ValueError(f"Неизвестный {label}: {', '.join(unknown)}")

    return {
        "name": form.get("name", "").strip()[:255],
        "description": form.get("description", "").strip() or None,
        "content_types": list(content_types),
        "analysis_types": list(analysis_types),
        "scope": _json_object(form.get("scope", ""), "Параметры анализа (scope)"),
        "trigger_type": trigger_type,
        "trigger_config": _json_object(form.get("trigger_config", ""), "Параметры триггера"),
        "action_type": action_type,
        "analyze_type": analyze_type,
        "is_active": form.get("is_active") == "on",
        "requires_approval": form.get("requires_approval") == "on",
        "is_default": form.get("is_default") == "on",
        "rate_limit_per_hour": _optional_int(form.get("rate_limit_per_hour", "")),
        "cooldown_seconds": _optional_int(form.get("cooldown_seconds", "")),
        "max_tokens": _optional_int(form.get("max_tokens", "")),
        "blacklist": _comma_list(form.get("blacklist", "")),
        "whitelist": _comma_list(form.get("whitelist", "")),
        "text_prompt": form.get("text_prompt", "").strip() or None,
        "image_prompt": form.get("image_prompt", "").strip() or None,
        "video_prompt": form.get("video_prompt", "").strip() or None,
        "audio_prompt": form.get("audio_prompt", "").strip() or None,
        "unified_summary_prompt": form.get("unified_summary_prompt", "").strip() or None,
        "llm_strategy": llm_strategy,
        "text_llm_model_id": _optional_model("text_llm_model_id"),
        "image_llm_model_id": _optional_model("image_llm_model_id"),
        "video_llm_model_id": _optional_model("video_llm_model_id"),
    }


@router.post("/{scenario_id}")
async def scenario_save(
    request: Request,
    scenario_id: int,
    token: str = Form("", alias="_csrf"),
):
    """Persist the editor. Parsing errors flash and return to the editor."""
    back = f"/app/scenarios/{scenario_id}"

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    denied = guard_web(request, "agentscenario", "update", back=back)
    if denied is not None:
        return denied

    scenario = await _own_scenario(request, scenario_id)
    if scenario is None:
        add_flash(request, "error", "Сценарий не найден")
        return RedirectResponse("/app/scenarios", status_code=302)

    # Read the raw body: the editor posts multi-value fields (content_types,
    # analysis_types), which a plain `Form(...)` signature cannot express.
    form = await request.form()
    try:
        fields = await _collect_fields(form)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    # When is_default is toggled on, clear it on every other scenario in this
    # workspace so there is always at most one default.
    if fields.get("is_default"):
        await AgentScenario.objects.filter(tenant_id=request.state.tenant_id, id__ne=scenario.id).update(
            is_default=False
        )

    await AgentScenario.objects.update_by_id(scenario.id, **fields)
    add_flash(request, "success", f"Сценарий «{fields['name']}» сохранён")
    return RedirectResponse(back, status_code=302)


@router.post("/{scenario_id}/test-trigger")
async def scenario_test_trigger(
    request: Request,
    scenario_id: int,
    sample_text: str = Form("", alias="sample_text"),
    analysis_json: str = Form("", alias="analysis_json"),
    token: str = Form("", alias="_csrf"),
):
    """Dry-run the scenario's trigger against sample text — no LLM, no writes.

    The same `TriggerEvaluator` the pipeline calls decides the outcome, so the
    answer is the real one; a hand-rolled preview would drift from production.
    """
    back = f"/app/scenarios/{scenario_id}"

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    denied = guard_web(request, "agentscenario", "update", back=back)
    if denied is not None:
        return denied

    scenario = await _own_scenario(request, scenario_id)
    if scenario is None:
        add_flash(request, "error", "Сценарий не найден")
        return RedirectResponse("/app/scenarios", status_code=302)

    if not scenario.trigger_type:
        add_flash(request, "info", "Триггер не задан — контент проходит без фильтрации")
        return RedirectResponse(back, status_code=302)

    if not sample_text.strip():
        add_flash(request, "error", "Вставьте текст поста для проверки")
        return RedirectResponse(back, status_code=302)

    from app.services.ai.trigger_evaluator import TriggerEvaluator

    content = [{"text": sample_text.strip()}]
    evaluator = TriggerEvaluator()
    kept = await evaluator.should_analyze(content, scenario)

    # Post-analysis triggers (sentiment threshold, manual) only decide after the
    # LLM has run, so the JSON box is what feeds them.
    if analysis_json.strip():
        try:
            result = _json_object(analysis_json, "Результат анализа (JSON)")
        except ValueError as e:
            add_flash(request, "error", str(e))
            return RedirectResponse(back, status_code=302)
        acts = await evaluator.should_act(result, scenario)
        add_flash(request, "success", f"Действие: {'выполнится' if acts else 'пропустится'}")
        return RedirectResponse(back, status_code=302)

    if kept:
        add_flash(request, "success", f"Текст прошёл триггер «{scenario.trigger_type.display_name}»")
    else:
        add_flash(request, "info", f"Текст отсечён триггером «{scenario.trigger_type.display_name}»")
    return RedirectResponse(back, status_code=302)
