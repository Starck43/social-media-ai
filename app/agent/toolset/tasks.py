"""Tools: manage cron agent tasks (list / add / remove / pause)."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool


def _get_job_types() -> tuple[str, ...]:
    from app.jobs.handlers import HANDLERS

    return tuple(HANDLERS.keys())


# What `task_add` must carry.
#
# These used to be only described in the schema, so a model that sent `name`
# and a schedule got a task whose `job_type` was empty (scheduled, never
# readable) or whose `start_date` was missing (first run drained the whole
# history). A missing required field is now refused at the boundary, together
# with what is missing, so the same turn can ask the owner instead of creating
# a half-configured task.
_REQUIRED_START_DATE = ("start_date", "с какой даты собирать контент")


def _missing_task_fields(
    *,
    cron: str,
    job_type: str,
    start_date: str | None,
) -> list[str]:
    """Names of the fields this task must carry but does not."""
    from app.models.managers.agent_task_manager import AgentTaskManager

    missing: list[str] = []
    if not str(job_type or "").strip():
        missing.append("job_type (что запускать)")
    if not str(cron or "").strip():
        missing.append("cron (расписание)")
    if (
        job_type
        and AgentTaskManager.requires_content_dates(job_type)
        and not AgentTaskManager.parse_date(start_date)
    ):
        missing.append(_REQUIRED_START_DATE[0] + " (YYYY-MM-DD — " + _REQUIRED_START_DATE[1] + ")")
    return missing


@tool(
    name="task_list",
    description="Показать все задачи (cron): имя, выражение, тип задачи, активность, время следующего запуска.",
    parameters={"type": "object", "properties": {}, "required": []},
    required_permission="scheduling.task.view",
)
async def task_list() -> list[dict[str, Any]]:
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from app.core.config import settings

    rows = await AgentTask.objects.order_by(AgentTask.id)
    tz = ZoneInfo(settings.SCHEDULER_TIMEZONE)

    def _fmt(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M")

    return [
        {
            "name": t.name,
            "cron": t.cron_expr,
            "job_type": t.job_type,
            "active": t.is_active,
            "next_run_at": _fmt(t.next_run_at),
            "last_status": t.last_status,
        }
        for t in rows
    ]


@tool(
    name="task_add",
    description=(
        "Создать cron-задачу. Примеры: «каждый день в 9:00» → '0 9 * * *', "
        "«каждый час» → '0 * * * *'. Тип задачи: collect, digest, prune, analyze, learn, reflect."
        " Источники задаются списком source_ids (связываются с задачей); пустой список = все активные. "
        "Выборка задаётся задачей: tracked_users — авторы (platform:external_id), чьи записи анализируются "
        "из источников задачи (пусто — материалы самих источников); keywords_list + keywords_match — фразы, "
        "которые должны быть в записи. Типы контента (посты, комментарии…) "
        "задаются сценарием, а не задачей. "
        "Обязательные поля: name, cron, job_type; для collect/analyze ещё start_date (с какой даты собирать "
        "контент, YYYY-MM-DD) — иначе первый запуск вытянет всё с первого поста; для анализа — scenario_id. "
        "Без них вызов отклоняется со списком пропущенного: не создавай задачу, а спроси владельца. "
        "Конкретные цели анализа (brands, competitors, hashtags, influencer_names) кладутся в payload "
        "задачи, а не в сценарий: сценарий — это методика, задача — конкретика. Если сценарий требует "
        "цель, а она не задана, результат вернёт warnings."
    ),
    confirm=True,
    required_permission="scheduling.task.create",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Уникальное имя задачи"},
            "cron": {
                "type": "string",
                "description": (
                    "Расписание: cron-выражение из 5 полей (мин час день месяц день-недели) "
                    "или @once — разовый запуск через минуту после создания"
                ),
            },
            "deactivate_after_success": {
                "type": "boolean",
                "description": (
                    "Пробный прогон: задача выключается после первого успешного выполнения. "
                    "Спрашивай владельца при создании задачи; по умолчанию false — "
                    "расписание работает и дальше"
                ),
            },
            "job_type": {"type": "string", "enum": list(_get_job_types()), "description": "Что запускать"},
            "source_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "ID источников для задачи (связываются через m2m). Пусто = все активные",
            },
            "tracked_users": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Опционально: авторы platform:external_id, чьи записи анализируются "
                "(фильтр по автору, не цель сбора)",
            },
            "keywords_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Опционально: фразы, которые должны быть в записи, чтобы её анализировали",
            },
            "keywords_match": {
                "type": "string",
                "enum": ["any", "all"],
                "description": "any — любая из фраз, all — все (по умолчанию any)",
            },
            "scenario_id": {"type": "integer", "description": "Опционально: ID сценария агента"},
            "period": {"type": "string", "description": "Период для digest: day | week (default day)"},
            "start_date": {
                "type": "string",
                "description": "Обязательно для collect/analyze: с какой даты собирать контент (YYYY-MM-DD)",
            },
            "end_date": {"type": "string", "description": "Опционально: до какой даты собирать контент (YYYY-MM-DD)"},
            "force_refresh": {
                "type": "boolean",
                "description": "Перетирать данные за период на каждом запуске (collect)",
            },
            "force_reanalyze": {
                "type": "boolean",
                "description": "Принудительно повторно анализировать уже обработанные записи (analyze)",
            },
            "brands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Бренды для отслеживания (для сценария с brand_mentions)",
            },
            "competitors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Конкуренты для анализа (для сценария с competitor)",
            },
            "hashtags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Хэштеги для отслеживания (для сценария с hashtag_analysis)",
            },
            "influencer_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Авторы/инфлюенсеры для анализа (для сценария с influencer)",
            },
            "keywords_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ключевые слова для поиска (для сценария с keywords)",
            },
            "topic_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Темы для анализа (для сценария с topics)",
            },
        },
        "required": ["name", "cron", "job_type"],
    },
)
async def task_add(
    name: str,
    cron: str,
    job_type: str,
    source_ids: list[int] | None = None,
    tracked_users: list[str] | None = None,
    keywords_list: list[str] | None = None,
    keywords_match: str | None = None,
    scenario_id: int | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    force_refresh: bool = False,
    force_reanalyze: bool = False,
    deactivate_after_success: bool = False,
    brands: list[str] | None = None,
    competitors: list[str] | None = None,
    hashtags: list[str] | None = None,
    influencer_names: list[str] | None = None,
    topic_list: list[str] | None = None,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    from app.core.tenant_context import current_tenant_id
    from app.models import AgentTask, Tenant
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.tasks.cron import next_run_at, resolve_tz

    missing = _missing_task_fields(cron=cron, job_type=job_type, start_date=start_date)
    if missing:
        # Refused before anything is written: the same turn asks the owner for
        # what is missing instead of leaving a scheduled task nobody can run.
        return {
            "error": "Не хватает данных задачи: " + ", ".join(missing),
            "missing": [value.split(" ", 1)[0] for value in missing],
        }

    job_types = _get_job_types()
    if job_type not in job_types:
        return {"error": f"Unknown job_type: {job_type!r}. Use one of: {', '.join(job_types)}"}
    if not AgentTaskManager.validate_cron(cron):
        return {"error": f"Invalid cron expression: {cron!r}"}
    if await AgentTask.objects.get(name=name):
        return {"error": f"Task {name!r} already exists"}

    from app.services.monitoring.selection import SelectionError

    payload: dict[str, Any] = {}
    try:
        payload = AgentTaskManager.build_selection_payload(
            tracked_users=tracked_users,
            keywords=keywords_list,
            keywords_match=keywords_match,
            existing=payload,
        )
    except SelectionError as error:
        return {"error": f"Некорректная выборка задачи: {error}"}
    if deactivate_after_success:
        # A trial run the owner agreed to stop after success. Stored on the
        # task, so the scheduler reads the same decision the owner made.
        payload["deactivate_after_success"] = True
    if period:
        payload["period"] = period
    # Specific analysis targets (brands, competitors, hashtags, …) live on the
    # task, never on the shared scenario — see `app/services/ai/param_registry.py`.
    for key, value in (
        ("brands", brands),
        ("competitors", competitors),
        ("hashtags", hashtags),
        ("influencer_names", influencer_names),
    ):
        if value:
            payload[key] = list(value)

    # collect/analyze need a content start date — without it a fresh source
    # drains the whole history from the first post (same rule as the web form).
    if AgentTaskManager.requires_content_dates(job_type):
        start = AgentTaskManager.parse_date(start_date)
        if start is None:
            return {"error": "Для задачи этого типа укажите start_date (с какой даты собирать контент, YYYY-MM-DD)"}
        end = AgentTaskManager.parse_date(end_date)
        payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=bool(force_refresh)))
        if force_reanalyze:
            payload["force_reanalyze"] = True

    # The workspace zone, same rule as the web form and the runner: a task
    # created here in the global zone would shift by the offset on its first fire.
    tenant = await Tenant.objects.get(id=current_tenant_id())
    tz = resolve_tz(tenant)
    task = await AgentTask.objects.create(
        name=name,
        cron_expr=cron,
        job_type=job_type,
        payload=payload,
        agent_scenario_id=scenario_id,
        is_active=True,
        next_run_at=next_run_at(cron, tz),
    )

    if source_ids:
        from app.models.managers.agent_task_manager import AgentTaskManager

        # The m2m table has no tenant column, so a link written here is the
        # only thing keeping a task out of another workspace's sources. Verify
        # them against the task's own workspace, like the CLI and web do.
        await _check_task_sources(source_ids, task.tenant_id)
        await AgentTaskManager().add_sources(task.id, source_ids)

    result: dict[str, Any] = {
        "status": "created",
        "name": task.name,
        "next_run_at": (
            task.next_run_at.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M") if task.next_run_at else None
        ),
    }

    # Hint when the bound scenario wants specific targets the task does not
    # carry — the analysis would otherwise have nothing to aim at. Surfaced as
    # a warning, never a hard error: brand_mentions without a brand list can
    # still detect mentions, just unfocused.
    if scenario_id:
        from app.models import AgentScenario
        from app.services.ai.param_registry import missing_target_params

        scenario = await AgentScenario.objects.get(id=scenario_id)
        if scenario is not None:
            missing = missing_target_params(scenario.analysis_types, payload)
            if missing:
                result["warnings"] = [
                    f"Сценарий «{scenario.name}» использует {m}, но в задаче не указан" for m in missing
                ]

    return result


async def _check_task_sources(source_ids: list[int], tenant_id: int) -> None:
    """Reject source ids that are missing or belong to another workspace."""
    from app.models import Source

    if not source_ids:
        return
    sources = {s.id: s for s in await Source.objects.filter(id__in=source_ids)}
    foreign = sorted(sid for sid, s in sources.items() if s.tenant_id != tenant_id)
    if foreign:
        raise ValueError(f"Source(s) {', '.join(map(str, foreign))} belong to another workspace")


@tool(
    name="task_remove",
    description="Удалить задачу по имени.",
    confirm=True,
    required_permission="scheduling.task.delete",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Имя задачи"}},
        "required": ["name"],
    },
)
async def task_remove(name: str) -> dict[str, Any]:
    from app.models import AgentTask

    deleted = await AgentTask.objects.delete(name=name)
    return {"status": "deleted" if deleted else "not_found", "name": name}


@tool(
    name="task_update",
    description=(
        "Изменить существующую задачу. Можно поменять: job_type (collect, digest, prune, analyze, learn, reflect), "
        "cron-расписание, payload (tracked_users, keywords_list, brands, competitors и т.д.), "
        "scenario_id, source_ids. Передавайте только те поля, которые нужно изменить. "
        "Неизменные поля остаются текущими. job_type можно сменить полностью."
    ),
    confirm=True,
    required_permission="scheduling.task.update",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Имя задачи для изменения"},
            "job_type": {
                "type": "string",
                "enum": list(_get_job_types()),
                "description": "Новый тип задачи: collect, digest, prune, analyze, learn, reflect",
            },
            "cron": {
                "type": "string",
                "description": "Новое cron-выражение (5 полей) или @once",
            },
            "deactivate_after_success": {
                "type": "boolean",
                "description": "Пробный прогон: выключить задачу после первого успешного выполнения",
            },
            "source_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Новый список source IDs (пусто = все активные)",
            },
            "tracked_users": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список авторов platform:external_id (пусто = материалы источников)",
            },
            "keywords_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список фраз отбора (пусто = без отбора по тексту)",
            },
            "keywords_match": {
                "type": "string",
                "enum": ["any", "all"],
                "description": "any — любая из фраз, all — все",
            },
            "scenario_id": {"type": "integer", "description": "Новый ID сценария агента"},
            "period": {"type": "string", "description": "Период для digest: day | week"},
            "start_date": {
                "type": "string",
                "description": "Новая start_date (YYYY-MM-DD) для collect/analyze",
            },
            "end_date": {"type": "string", "description": "Новая end_date (YYYY-MM-DD)"},
            "force_refresh": {
                "type": "boolean",
                "description": "Новое значение force_refresh (collect)",
            },
            "force_reanalyze": {
                "type": "boolean",
                "description": "Новое значение force_reanalyze (analyze)",
            },
            "brands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список брендов",
            },
            "competitors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список конкурентов",
            },
            "hashtags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список хэштегов",
            },
            "influencer_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список инфлюенсеров",
            },
            "keywords_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список ключевых слов",
            },
            "topic_list": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Новый список тем",
            },
        },
        "required": ["name"],
    },
)
async def task_update(
    name: str,
    job_type: str | None = None,
    cron: str | None = None,
    source_ids: list[int] | None = None,
    tracked_users: list[str] | None = None,
    keywords_list: list[str] | None = None,
    keywords_match: str | None = None,
    scenario_id: int | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    force_refresh: bool | None = None,
    force_reanalyze: bool | None = None,
    brands: list[str] | None = None,
    competitors: list[str] | None = None,
    hashtags: list[str] | None = None,
    influencer_names: list[str] | None = None,
    topic_list: list[str] | None = None,
    deactivate_after_success: bool | None = None,
) -> dict[str, Any]:
    from app.core.tenant_context import current_tenant_id
    from app.models import AgentTask, Tenant
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.tasks.cron import next_run_at, resolve_tz

    task = await AgentTask.objects.get(name=name)
    if task is None:
        return {"error": f"Task {name!r} not found"}

    job_types = _get_job_types()
    if job_type is not None and job_type not in job_types:
        return {"error": f"Unknown job_type: {job_type!r}. Use one of: {', '.join(job_types)}"}
    if cron is not None and not AgentTaskManager.validate_cron(cron):
        return {"error": f"Invalid cron expression: {cron!r}"}

    from app.services.monitoring.selection import SelectionError

    # Build updated payload by merging into existing payload
    updated_payload = dict(task.payload) if task.payload else {}
    # The removed author-selection key never survives a task update.
    try:
        updated_payload = AgentTaskManager.build_selection_payload(
            tracked_users=tracked_users,
            keywords=keywords_list,
            keywords_match=keywords_match,
            existing=updated_payload,
        )
    except SelectionError as error:
        return {"error": f"Некорректная выборка задачи: {error}"}
    if period is not None:
        updated_payload["period"] = period
    for key, value in (
        ("brands", brands),
        ("competitors", competitors),
        ("hashtags", hashtags),
        ("influencer_names", influencer_names),
        ("topic_list", topic_list),
    ):
        if value is not None:
            updated_payload[key] = list(value)

    # Handle date fields for collect/analyze
    _existing_cli_dates = (task.payload or {}).get("cli_dates", {})
    if job_type is not None and AgentTaskManager.requires_content_dates(job_type):
        start = AgentTaskManager.parse_date(start_date)
        if start is None:
            start = AgentTaskManager.parse_date(_existing_cli_dates.get("start_date"))
        end = AgentTaskManager.parse_date(end_date)
        if end is None:
            end = AgentTaskManager.parse_date(_existing_cli_dates.get("end_date"))
        fr = force_refresh if force_refresh is not None else task.payload.get("force_refresh", False)
        updated_payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=fr))
        if force_reanalyze is not None:
            updated_payload["force_reanalyze"] = force_reanalyze
    elif job_type is None and AgentTaskManager.requires_content_dates(task.job_type):
        # job_type unchanged but dates provided
        if start_date or end_date or force_refresh is not None:
            start = AgentTaskManager.parse_date(start_date) if start_date else None
            end = AgentTaskManager.parse_date(end_date) if end_date else None
            fr = force_refresh if force_refresh is not None else task.payload.get("force_refresh", False)
            updated_payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=fr))
            if force_reanalyze is not None:
                updated_payload["force_reanalyze"] = force_reanalyze

    # Determine new job_type
    new_job_type = job_type if job_type is not None else task.job_type
    new_cron = cron if cron is not None else task.cron_expr

    if deactivate_after_success is not None:
        # Same key the scheduler reads; clearing it returns the task to a
        # normal recurring schedule.
        if deactivate_after_success:
            updated_payload["deactivate_after_success"] = True
        else:
            updated_payload.pop("deactivate_after_success", None)

    # Compute next_run_at if cron changed
    tenant = await Tenant.objects.get(id=current_tenant_id())
    updates: dict[str, Any] = {
        "job_type": new_job_type,
        "payload": updated_payload,
    }
    # A field the caller did not send keeps its current value. Writing
    # `agent_scenario_id: None` on every update silently unbound the task's
    # scenario — the analysis then ran without its methodology. The same
    # happened to the trial flag, which was set after the payload was written
    # and therefore never saved at all.
    if scenario_id is not None:
        updates["agent_scenario_id"] = scenario_id
    if cron is not None:
        updates["next_run_at"] = next_run_at(new_cron, resolve_tz(tenant))
    if deactivate_after_success is not None:
        # A trial task gets a live schedule back: it had been switched off
        # after its success, so re-arming means the next run must be planned.
        updates["is_active"] = True
        if cron is None:
            updates["next_run_at"] = next_run_at(new_cron, resolve_tz(tenant))

    await AgentTask.objects.update_by_id(task.id, **updates)

    # Update sources if provided
    if source_ids is not None:
        await _check_task_sources(source_ids, task.tenant_id)
        await AgentTaskManager().add_sources(task.id, source_ids)

    changes: dict[str, Any] = {"status": "updated", "name": task.name}
    if job_type:
        changes["old_job_type"] = task.job_type
        changes["new_job_type"] = job_type
    if cron:
        changes["old_cron"] = task.cron_expr
        changes["new_cron"] = cron
    if source_ids is not None:
        changes["sources_updated"] = True
    return changes


@tool(
    name="task_pause",
    description="Поставить задачу на паузу или возобновить её.",
    confirm=True,
    required_permission="scheduling.task.update",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Имя задачи"},
            "pause": {
                "type": "boolean",
                "description": "true = поставить на паузу (по умолчанию), false = возобновить",
            },
        },
        "required": ["name"],
    },
)
async def task_pause(name: str, pause: bool = True) -> dict[str, Any]:
    from app.models import AgentTask

    task = await AgentTask.objects.get(name=name)
    if task is None:
        return {"error": f"Task {name!r} not found"}
    await AgentTask.objects.update_by_id(task.id, is_active=not pause)
    return {"status": "paused" if pause else "resumed", "name": name}
