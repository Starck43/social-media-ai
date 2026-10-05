"""Scenarios `/app/scenarios` — list, create and manage bot scenarios.

Creation and editing go through one 4-step wizard (`scenario_wizard.html`)
that needs no JSON: the user ticks analysis/content types and the scope is
generated for them; the preview shows the final prompt before it is saved.
The shared logic lives in `app/services/ai/scenario_builder.py`, which the
admin form reuses too.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func

from app.models.agent_scenario import AgentScenario

from .deps import add_flash, ensure_csrf, guard_web, render

logger = logging.getLogger(__name__)

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


# ── editor helpers ──────────────────────────────────────────────────────────


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
    """Parse a JSON object field, with the field name in the error message."""
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


async def _own_task(request: Request, scenario_id: int) -> "AgentTask | None":
    """One active task of this workspace using the scenario, or None.

    The trigger now belongs to the task, so a scenario detail page can only
    preview a trigger by borrowing one of the tasks that actually runs it. With
    several tasks sharing the scenario they may carry different triggers, and
    this returns the first — the page says which one it is rather than implying
    it is the scenario's single behaviour.
    """
    from app.models import AgentTask

    return await (
        AgentTask.objects.filter(
            agent_scenario_id=scenario_id,
            tenant_id=request.state.tenant_id,
            is_active=True,
        )
        .order_by("id")
        .first()
    )


def _enum_by_value(enum_cls, raw: str):
    """Resolve a form value to its enum member, or None when unknown/blank.

    Both shapes are accepted — `db_value` (what the select options carry) and
    `name` — so a form written against either shape keeps working, and the
    blank option still maps to None rather than raising.
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


# ── the 4-step wizard ───────────────────────────────────────────────────────


@router.get("/new")
async def scenario_new(request: Request):
    """Start the step-by-step wizard for a new scenario (no JSON required).

    Empty fields start from what the owner chose before (`scenario_prefs`), so a
    second scenario is a couple of clicks rather than a re-type: the grouping
    mode and the brand list are usually the same.
    """
    denied = guard_web(request, "agentscenario", "create", back="/app/scenarios")
    if denied is not None:
        return denied

    from app.services.ai import scenario_prefs
    from app.services.ai.scenario_builder import ScenarioDraft

    draft = await scenario_prefs.apply_to_draft(ScenarioDraft(name="", is_active=True))
    return await _wizard_page(request, draft=draft, create=True)


@router.get("/{scenario_id}")
async def scenario_detail(request: Request, scenario_id: int):
    """Read + edit one scenario through the same 4-step wizard as create.

    The scenario is looked up through the manager, so a foreign id reads as
    "not found" rather than leaking another workspace's prompts. Edit renders
    the wizard pre-filled with the stored draft.
    """
    scenario = await _own_scenario(request, scenario_id)
    if scenario is None:
        add_flash(request, "error", "Сценарий не найден")
        return RedirectResponse("/app/scenarios", status_code=302)

    from app.services.ai.scenario_builder import ScenarioBuilder

    draft = ScenarioBuilder.draft_from_scenario(scenario)
    return await _wizard_page(request, draft=draft, create=False, scenario_id=scenario.id)


@router.get("/{scenario_id}/preview")
async def scenario_preview(request: Request, scenario_id: int):
    """The final-prompt preview for an existing scenario."""
    scenario = await _own_scenario(request, scenario_id)
    if scenario is None:
        add_flash(request, "error", "Сценарий не найден")
        return RedirectResponse("/app/scenarios", status_code=302)

    from app.services.ai.scenario_builder import ScenarioBuilder

    draft = ScenarioBuilder.draft_from_scenario(scenario)
    blocks = ScenarioBuilder.preview_blocks(draft)
    return render(
        request,
        "web/scenario_preview.html",
        section="scenarios",
        scenario=scenario,
        blocks=blocks,
        base_media=blocks["base_media"],
    )


@router.post("/preview")
async def scenario_preview_build(request: Request):
    """Live final-prompt preview from the current (unsaved) wizard form.

    Reuses the production `PromptBuilder` + `JSONSchemaBuilder`, so the reader
    sees exactly what the analyzer would send. No write, no LLM call.
    """
    from app.services.ai.scenario_builder import ScenarioBuilder

    try:
        draft = _draft_from_form(await request.form())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    blocks = ScenarioBuilder.preview_blocks(draft)
    return render(
        request,
        "web/scenario_preview.html",
        section="scenarios",
        scenario=None,
        blocks=blocks,
        base_media=blocks["base_media"],
        csrf="",
        flashes=[],
        user=getattr(request.state, "web_user", None),
        memberships=getattr(request.state, "memberships", []) or [],
        workspaces=getattr(request.state, "workspaces", []) or [],
        tenant=getattr(request.state, "tenant", None),
        perms=getattr(request.state, "web_perms", None),
    )


@router.post("/suggest-prompt")
async def scenario_suggest_prompt(request: Request):
    """Turn a plain description into a `base_prompt` via the LLM (Step 4).

    The user writes what they want to monitor in their own words; the model
    answers with a ready analysis prompt. The fleet may be unconfigured or a
    call may time out — then the response says so and the wizard falls back to
    the manual field. Nothing is written here, only a prompt is generated.
    """
    form = await request.form()
    token = form.get("_csrf") or ""
    if not ensure_csrf(request, token):
        return JSONResponse({"error": "Сессия истекла, попробуйте ещё раз"}, status_code=400)

    description = (form.get("description") or "").strip()
    if not description:
        return JSONResponse({"error": "Опишите задачу — так модели будет что превратить в промпт"}, status_code=400)

    generated = await _suggest_base_prompt(description)
    if not generated:
        return JSONResponse({"fallback": True, "message": "Модель сейчас недоступна — напишите промпт вручную."})
    return JSONResponse({"fallback": False, "prompt": generated})


async def _suggest_base_prompt(description: str) -> str | None:
    """Ask the fleet for a base_prompt; None when no model answers.

    Delegates to `scenario_prompt_suggest`, the same helper the chat tool
    `scenario_suggest_prompt` calls, so the wizard and the agent phrase the
    request identically and degrade the same way.
    """
    from app.services.ai.scenario_prompt_suggest import suggest_base_prompt

    return await suggest_base_prompt(description)


def _draft_from_form(form) -> "ScenarioDraft":
    """Build a wizard draft from the posted form (create, edit and preview share it).

    The wizard is deliberately JSON-free: analysis types and content types
    arrive as checkbox values, the scope is re-generated from the ticked
    analysis types (so the user never writes JSON), and the prompt is a
    media-agnostic `base_prompt` plus optional per-media `media_overrides`
    textareas (assembled into the JSON column here). Raises `ValueError` for
    unknown enum values.
    """
    from app.services.ai.scenario_builder import ScenarioBuilder, ScenarioDraft
    from app.types import AnalysisType, ContentType
    from app.types.enums.bot_types import AnalyzeType

    def _getlist(key: str) -> list[str]:
        return form.getlist(key) if hasattr(form, "getlist") else (form.get(key) or [])

    content_types = [v for v in _getlist("content_types") if v]
    analysis_types = [v for v in _getlist("analysis_types") if v]

    for raw, enum_cls, label in (
        (content_types, ContentType, "тип контента"),
        (analysis_types, AnalysisType, "тип анализа"),
    ):
        unknown = [v for v in raw if _enum_by_value(enum_cls, v) is None]
        if unknown:
            raise ValueError(f"Неизвестный {label}: {', '.join(unknown)}")

    analyze_type = None
    if form.get("analyze_type"):
        analyze_member = _enum_by_value(AnalyzeType, form.get("analyze_type"))
        if analyze_member is None:
            raise ValueError(f"Неизвестный режим анализа: {form['analyze_type']}")
        analyze_type = analyze_member.db_value

    def _opt_int(key: str) -> int | None:
        raw = (form.get(key) or "").strip()
        return _optional_int(raw) if raw else None

    def _opt_model(key: str) -> int | None:
        raw = (form.get(key) or "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise ValueError("Выберите модель из списка")
        return int(raw)

    def _opt_prompt(key: str) -> str | None:
        return (form.get(key) or "").strip() or None

    try:
        scope_raw = _json_object(form.get("scope", "") or "", "Параметры анализа (scope)")
    except ValueError:
        # The wizard stores scope as JSON only as an escape hatch; an invalid
        # body means the user hand-edited it, which the wizard does not require.
        scope_raw = {}

    if not scope_raw:
        # The wizard requires no JSON: with no hand-edited scope the template
        # is generated from the ticked analysis types.
        scope = ScenarioBuilder.build_scope_template(analysis_types)
    else:
        scope = ScenarioBuilder.sanitize_scope(scope_raw, analysis_types)

    return ScenarioDraft(
        name=form.get("name", "").strip()[:255],
        description=form.get("description", "").strip() or None,
        is_active=form.get("is_active") == "on",
        is_default=form.get("is_default") == "on",
        content_types=content_types,
        analysis_types=analysis_types,
        analyze_type=analyze_type,
        scope=scope,
        base_prompt=_opt_prompt("base_prompt"),
        media_overrides={
            media: prompt
            for media, prompt in (
                ("image", _opt_prompt("media_overrides_image")),
                ("video", _opt_prompt("media_overrides_video")),
                ("audio", _opt_prompt("media_overrides_audio")),
            )
            if prompt
        },
        summary_prompt=_opt_prompt("summary_prompt"),
        llm_strategy=form.get("llm_strategy", "").strip() or None,
        text_llm_model_id=_opt_model("text_llm_model_id"),
        image_llm_model_id=_opt_model("image_llm_model_id"),
        video_llm_model_id=_opt_model("video_llm_model_id"),
        max_tokens=_opt_int("max_tokens"),
    )


def _prompt_warnings(draft: "ScenarioDraft") -> list[str]:
    """Unknown prompt variables across the draft's prompt fields (Phase 1.5).

    The docs' validation contract is a *warning*: the scenario is saved, but the
    caller is told which `{var}` names will not be substituted. One message per
    field, Russian (the web UI language).
    """
    from app.services.ai.prompt_variables import PromptVariables

    def _message(label: str, prompt: str) -> str | None:
        unknown = PromptVariables.validate_prompt(prompt)
        if not unknown:
            return None
        names = ", ".join(f"{{{name}}}" for name in unknown)
        return f"{label}: незнакомые переменные {names} — не будут подставлены при анализе"

    messages = [
        m
        for m in (
            _message("Промпт анализа", draft.base_prompt),
            _message("Сводный промпт", draft.summary_prompt),
            *[
                _message(f"Переопределение для {media}", prompt)
                for media, prompt in (draft.media_overrides or {}).items()
            ],
        )
        if m
    ]
    return messages


async def _remember_preferences(scenario) -> None:
    """Store what this scenario was saved with as defaults for the next one.

    Same source the chat tools write, so a scenario made in the browser and one
    made in the chat teach the agent the same preferences. Best-effort: a
    preference row that fails to write must not fail the save the owner asked
    for.
    """
    from app.services.ai import scenario_prefs
    from app.services.ai.scenario_builder import ScenarioBuilder

    try:
        draft = ScenarioBuilder.draft_from_scenario(scenario)
        await scenario_prefs.remember(
            preferred_analyze_type=draft.analyze_type,
            brands=(draft.scope or {}).get("competitor", {}).get("competitor_list") or [],
        )
    except Exception:  # noqa: BLE001 — preferences are a convenience, not the work
        logger.warning("Could not store scenario preferences", exc_info=True)


async def _wizard_page(request: Request, *, draft, create: bool, scenario_id: int | None = None):
    """Render the 4-step wizard for a new or existing scenario."""
    from app.core.analysis_constants import ANALYSIS_TYPE_DEFAULTS
    from app.models.llm_model import LLMModel
    from app.services.ai.scenario_builder import ScenarioBuilder
    from app.types import AnalysisType, ContentType, LLMStrategyType
    from app.types.enums.bot_types import AnalyzeType

    perms = getattr(request.state, "web_perms", None)
    llm_models = [
        {"id": m.id, "name": m.name, "model_type": m.model_type}
        for m in await LLMModel.objects.filter(is_active=True).order_by(LLMModel.name)
    ]
    return render(
        request,
        "web/scenario_wizard.html",
        section="scenarios",
        # Step 4 («Предпросмотр») belongs to a platform role above ADMIN.
        show_preview_step=perms is not None and perms.is_superuser_role,
        create=create,
        scenario_id=scenario_id,
        draft=draft,
        content_types=list(ContentType),
        analysis_types=list(AnalysisType),
        analyze_types=list(AnalyzeType),
        llm_strategies=LLMStrategyType.choices(),
        llm_models=llm_models,
        media_types=ScenarioBuilder.media_types_for(draft.content_types),
        available_variables=ScenarioBuilder.base_variables(),
        analysis_defaults=ANALYSIS_TYPE_DEFAULTS,
        all_analysis_types=[at.db_value for at in AnalysisType],
        pretty_json=_pretty_json,
    )


@router.post("/new")
async def scenario_create(
    request: Request,
    name: str = Form(...),
    token: str = Form("", alias="_csrf"),
):
    """Create a scenario from the wizard; parsing errors flash and return."""
    from app.services.ai.scenario import PlanLimitError, scenario_service

    back = "/app/scenarios/new"
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    denied = guard_web(request, "agentscenario", "create", back=back)
    if denied is not None:
        return denied

    form = await request.form()
    try:
        draft = _draft_from_form(form)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    if not draft.name.strip():
        add_flash(request, "error", "Введите название сценария")
        return RedirectResponse(back, status_code=302)

    for warning in _prompt_warnings(draft):
        add_flash(request, "warning", warning)

    try:
        scenario = await scenario_service.create_scenario(
            name=draft.name,
            description=draft.description,
            tenant_id=request.state.tenant_id,
            analysis_types=draft.analysis_types,
            content_types=draft.content_types,
            scope=draft.scope,
            analyze_type=draft.analyze_type,
            base_prompt=draft.base_prompt,
            media_overrides=draft.media_overrides,
            summary_prompt=draft.summary_prompt,
            is_active=draft.is_active,
            is_default=draft.is_default,
            max_tokens=draft.max_tokens,
            llm_strategy=draft.llm_strategy,
            text_llm_model_id=draft.text_llm_model_id,
            image_llm_model_id=draft.image_llm_model_id,
            video_llm_model_id=draft.video_llm_model_id,
        )
    except PlanLimitError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    add_flash(request, "success", f"Сценарий «{scenario.name}» создан")
    await _remember_preferences(scenario)
    return RedirectResponse(f"/app/scenarios/{scenario.id}", status_code=302)


@router.post("/{scenario_id}")
async def scenario_save(
    request: Request,
    scenario_id: int,
    token: str = Form("", alias="_csrf"),
):
    """Persist the wizard (edit mode). Parsing errors flash and return.

    The draft preserves any custom scope keys a user added beyond the
    auto-template, so editing does not silently drop them.
    """
    from app.services.ai.scenario_builder import ScenarioBuilder

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

    # Read the raw body: the wizard posts multi-value fields (content_types,
    # analysis_types), which a plain `Form(...)` signature cannot express.
    form = await request.form()
    try:
        draft = _draft_from_form(form)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    # Editing must not destroy custom scope keys the auto-template does not own.
    draft.scope = {**(scenario.scope or {}), **draft.scope}

    for warning in _prompt_warnings(draft):
        add_flash(request, "warning", warning)

    # When is_default is toggled on, clear it on every other scenario in this
    # workspace so there is always at most one default.
    if draft.is_default:
        await AgentScenario.objects.filter(tenant_id=request.state.tenant_id, id__ne=scenario.id).update(
            is_default=False
        )

    ScenarioBuilder.apply_draft(scenario, draft)
    await AgentScenario.objects.update_by_id(
        scenario.id,
        name=scenario.name,
        description=scenario.description,
        content_types=scenario.content_types,
        analysis_types=scenario.analysis_types,
        scope=scenario.scope,
        analyze_type=scenario.analyze_type,
        is_active=scenario.is_active,
        is_default=scenario.is_default,
        max_tokens=scenario.max_tokens,
        base_prompt=scenario.base_prompt,
        media_overrides=scenario.media_overrides,
        summary_prompt=scenario.summary_prompt,
        llm_strategy=scenario.llm_strategy,
        text_llm_model_id=scenario.text_llm_model_id,
        image_llm_model_id=scenario.image_llm_model_id,
        video_llm_model_id=scenario.video_llm_model_id,
    )
    add_flash(request, "success", f"Сценарий «{draft.name}» сохранён")
    await _remember_preferences(scenario)
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

    # The trigger lives on the task now. Testing against the scenario would test
    # a configuration that no run reads any more.
    task = await _own_task(request, scenario.id)
    if task is None:
        add_flash(request, "info", "У сценария нет активных задач — триггер проверяется в разделе «Задачи»")
        return RedirectResponse(back, status_code=302)

    if not task.trigger_type:
        add_flash(request, "info", "Триггер не задан — контент проходит без фильтрации")
        return RedirectResponse(back, status_code=302)

    if not sample_text.strip():
        add_flash(request, "error", "Вставьте текст поста для проверки")
        return RedirectResponse(back, status_code=302)

    from app.services.ai.trigger_evaluator import TriggerEvaluator

    content = [{"text": sample_text.strip()}]
    evaluator = TriggerEvaluator()
    kept = await evaluator.should_analyze(content, task)

    # Post-analysis triggers (sentiment threshold, manual) only decide after the
    # LLM has run, so the JSON box is what feeds them.
    if analysis_json.strip():
        try:
            result = _json_object(analysis_json, "Результат анализа (JSON)")
        except ValueError as e:
            add_flash(request, "error", str(e))
            return RedirectResponse(back, status_code=302)
        acts = await evaluator.should_act(result, task)
        add_flash(request, "success", f"Действие: {'выполнится' if acts else 'пропустится'}")
        return RedirectResponse(back, status_code=302)

    if kept:
        add_flash(request, "success", f"Текст прошёл триггер «{task.trigger_type.display_name}»")
    else:
        add_flash(request, "info", f"Текст отсечён триггером «{task.trigger_type.display_name}»")
    return RedirectResponse(back, status_code=302)
