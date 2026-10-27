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
_MAX_PG_INTEGER = 2_147_483_647


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
    from app.models import AgentTask

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
            "id": t.id,
            "task_url": f"/app/tasks/{t.id}",
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
            "collection": {
                "type": "object",
                "description": (
                    "Что и где собирать (принадлежит задаче, не сценарию): content_types (posts|comments), "
                    "engagement_types (likes|reposts), limit, queries, city {name, mode}, "
                    "discovery {people, communities, queries}, comments {max_per_post, total}, "
                    "candidates {limit, confirm}, date_window {start, end}"
                ),
            },
            "task_plan": {
                "type": "object",
                "description": (
                    "Универсальный план соцзадачи: goal, platforms, source_scope/source_ids, collection, "
                    "window (fixed с start/end или rolling с days), scenario_id и trigger (once либо schedule "
                    "с cron/timezone). План задаёт источники, сценарий, сбор, окно и расписание."
                ),
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
    collection: dict[str, Any] | None = None,
    task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    from app.core.tenant_context import current_tenant_id
    from app.models import AgentTask, Tenant
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.tasks.cron import next_run_at, resolve_tz

    plan_block = None
    if task_plan is not None:
        from app.services.task_plans import TaskPlanError, apply_task_plan, parse_task_plan

        try:
            parsed_plan = parse_task_plan(task_plan)
            plan_block = dict(task_plan)
            expected_cron = parsed_plan.trigger.cron if parsed_plan.trigger.type == "schedule" else "@once"
            if cron != expected_cron:
                return {"error": "cron conflicts with task_plan.trigger; provide matching values"}
            if source_ids is not None and source_ids != list(parsed_plan.source_ids):
                return {"error": "source_ids conflicts with task_plan.source_ids"}
            if scenario_id is not None and scenario_id != parsed_plan.scenario_id:
                return {"error": "scenario_id conflicts with task_plan.scenario_id"}
            if collection is not None:
                return {"error": "collection is defined by task_plan; omit the legacy collection field"}
            if start_date is not None or end_date is not None:
                return {"error": "date fields are defined by task_plan.window; omit legacy date fields"}
            cron = expected_cron
            source_ids = list(parsed_plan.source_ids) if parsed_plan.source_scope == "selected" else []
            scenario_id = parsed_plan.scenario_id
            collection = dict(task_plan.get("collection") or {})
            if parsed_plan.window and parsed_plan.window.type == "fixed":
                start_date = parsed_plan.window.start.isoformat()
                end_date = parsed_plan.window.end.isoformat() if parsed_plan.window.end else None
        except TaskPlanError as error:
            return {"error": str(error)}

    missing = _missing_task_fields(cron=cron, job_type=job_type, start_date=start_date)
    if plan_block is not None and parsed_plan.window and parsed_plan.window.type == "rolling":
        missing = [item for item in missing if not item.startswith("start_date ")]
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
    if AgentTaskManager.requires_content_dates(job_type) and plan_block is None:
        start = AgentTaskManager.parse_date(start_date)
        if start is None:
            return {"error": "Для задачи этого типа укажите start_date (с какой даты собирать контент, YYYY-MM-DD)"}
        end = AgentTaskManager.parse_date(end_date)
        payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=bool(force_refresh)))
        if force_reanalyze:
            payload["force_reanalyze"] = True

    from app.services.collection.intent import (
        CollectionIntentError,
        apply_collection,
        collection_error_message,
    )

    try:
        payload = apply_collection(payload, collection or None, job_type=job_type)
    except CollectionIntentError as error:
        return {"error": collection_error_message(str(error))}

    if plan_block is not None:
        from app.services.task_plans import apply_task_plan

        try:
            payload = apply_task_plan(payload, plan_block, job_type=job_type)
        except ValueError as error:
            return {"error": str(error)}

    # The workspace zone, same rule as the web form and the runner: a task
    # created here in the global zone would shift by the offset on its first fire.
    tenant = await Tenant.objects.get(id=current_tenant_id())
    tz = resolve_tz(tenant)
    if plan_block is not None:
        from app.services.task_plans import task_plan_timezone

        try:
            tz = task_plan_timezone(plan_block, tz)
        except ValueError as error:
            return {"error": str(error)}
    from app.core.database import new_session

    session = new_session()
    try:
        async with session.begin():
            from app.models.managers.job_manager import JobManager

            if source_ids is not None and any(
                type(value) is not int or not 0 < value <= _MAX_PG_INTEGER for value in source_ids
            ):
                return {"error": "Source ids must be positive PostgreSQL INTEGER values"}
            await JobManager.lock_source_admission(session, current_tenant_id())
            task = await AgentTask.objects.create(
                session=session,
                name=name,
                cron_expr=cron,
                job_type=job_type,
                payload=payload,
                agent_scenario_id=scenario_id,
                is_active=plan_block is None,
                next_run_at=next_run_at(cron, tz),
            )
            if source_ids is not None:
                await AgentTaskManager().set_sources(task.id, source_ids, session=session)
    except ValueError as error:
        return {"error": str(error)}
    finally:
        await session.close()

    result: dict[str, Any] = {
        "status": "created",
        "id": task.id,
        "task_url": f"/app/tasks/{task.id}",
        "name": task.name,
        "next_run_at": (
            task.next_run_at.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M") if task.next_run_at else None
        ),
    }

    if plan_block is not None:
        try:
            from app.services.billing.payer import consent_current_user_as_task_payer

            await consent_current_user_as_task_payer(task.id)
            await AgentTask.objects.update_by_id(task.id, is_active=True)
            result["active"] = True
        except Exception:
            # Keep the task available for explicit setup, but never activate an
            # AI plan until the authenticated owner has consented to its payer.
            result["active"] = False
            result["billing_consent_required"] = True
            result["billing_error"] = "Настройте личный AI-бюджет и подтвердите оплату задачи в настройках"

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
        "scenario_id, source_ids. clear_scenario=true явно отвязывает сценарий; source_ids заменяет весь список. "
        "Передавайте только те поля, которые нужно изменить. "
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
                "description": "Новый точный список source IDs, заменяющий текущие связи; [] = все активные",
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
            "scenario_id": {
                "type": "integer",
                "description": "Новый ID сценария агента; не передавайте, чтобы сохранить текущий",
            },
            "clear_scenario": {
                "type": "boolean",
                "description": "true — отвязать сценарий; нельзя одновременно с scenario_id",
            },
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
            "collection": {
                "type": "object",
                "description": (
                    "Новый сбор задачи (content_types, engagement_types, limit, queries, city, discovery, "
                    "comments, candidates, date_window). Не передавайте, чтобы сохранить текущий; {} убирает сбор"
                ),
            },
            "task_plan": {
                "type": "object",
                "description": (
                    "Полная замена универсального плана соцзадачи (goal, platforms, source_scope/source_ids, "
                    "collection, window, scenario_id, trigger). При передаче omit legacy source_ids, "
                    "scenario_id, cron, collection и даты. Без task_plan существующий план сохраняется."
                ),
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
    clear_scenario: bool = False,
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
    collection: dict[str, Any] | None = None,
    task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.core.tenant_context import current_tenant_id
    from app.models import AgentTask, Tenant
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.tasks.cron import next_run_at, resolve_tz

    from app.core.database import new_session

    session = new_session()
    try:
        async with session.begin():
            manager = AgentTaskManager()
            task = await AgentTask.objects.get(name=name, session=session)
            if task is None:
                return {"error": f"Task {name!r} not found"}
            task = await manager._lock_task_for_mutation(session, task.id)
            if task is None:
                return {"error": f"Task {name!r} not found"}
            original_job_type = task.job_type
            original_cron = task.cron_expr
            existing_plan = (task.payload or {}).get("task_plan")
            from app.services.task_plans import prepare_task_plan_update

            policy_input = {}
            for key, value in (
                ("source_ids", source_ids),
                ("agent_scenario_id", scenario_id),
                ("cron_expr", cron),
                ("collection", collection),
                ("start_date", start_date),
                ("end_date", end_date),
            ):
                if value is not None:
                    policy_input[key] = value
            if clear_scenario:
                policy_input["agent_scenario_id"] = None
            if task_plan is not None:
                policy_input["task_plan"] = task_plan
            try:
                mutation = prepare_task_plan_update(task.payload, policy_input, job_type=job_type or task.job_type)
            except ValueError as error:
                return {"error": str(error)}
            parsed_plan = mutation.plan if task_plan is not None else None
            clearing_existing_plan = mutation.cleared
            if clearing_existing_plan:
                existing_plan = None
            if parsed_plan is not None:
                cron = mutation.changes["cron_expr"]
                source_ids = mutation.changes["source_ids"]
                scenario_id = parsed_plan.scenario_id
                collection = None
                if parsed_plan.window and parsed_plan.window.type == "fixed":
                    start_date = parsed_plan.window.start.isoformat()
                    end_date = parsed_plan.window.end.isoformat() if parsed_plan.window.end else None
            if source_ids is not None and any(
                type(value) is not int or not 0 < value <= _MAX_PG_INTEGER for value in source_ids
            ):
                return {"error": "Source ids must be positive PostgreSQL INTEGER values"}

            job_types = _get_job_types()
            if job_type is not None and job_type not in job_types:
                return {"error": f"Unknown job_type: {job_type!r}. Use one of: {', '.join(job_types)}"}
            if cron is not None and not AgentTaskManager.validate_cron(cron):
                return {"error": f"Invalid cron expression: {cron!r}"}
            if clear_scenario and scenario_id is not None:
                return {"error": "Нельзя одновременно передать scenario_id и clear_scenario=true"}

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

            for field, value in (("start_date", start_date), ("end_date", end_date)):
                if value is not None and AgentTaskManager.parse_date(value) is None:
                    return {"error": f"Некорректная {field}: {value!r} (ожидается YYYY-MM-DD)"}
            new_job_type = job_type if job_type is not None else task.job_type
            existing_dates = (task.payload or {}).get("cli_dates") or {}
            if AgentTaskManager.requires_content_dates(new_job_type) and parsed_plan is None and existing_plan is None:
                start = (
                    AgentTaskManager.parse_date(start_date)
                    if start_date is not None
                    else AgentTaskManager.parse_date(existing_dates.get("start_date"))
                )
                end = (
                    AgentTaskManager.parse_date(end_date)
                    if end_date is not None
                    else AgentTaskManager.parse_date(existing_dates.get("end_date"))
                )
                changed_type = job_type is not None and new_job_type != task.job_type
                changed_window = start_date is not None or end_date is not None or force_refresh is not None
                if start is None and (changed_type or changed_window):
                    return {
                        "error": "Для задачи этого типа укажите start_date "
                        "(с какой даты собирать контент, YYYY-MM-DD)"
                    }
                if start is not None and end is not None and start > end:
                    return {"error": "start_date не может быть позже end_date"}
                if start is not None and (changed_type or changed_window):
                    refresh = (
                        force_refresh
                        if force_refresh is not None
                        else bool((task.payload or {}).get("force_refresh", False))
                    )
                    updated_payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=refresh))
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

            from app.services.collection.intent import (
                CollectionIntentError,
                apply_collection,
                collection_error_message,
            )

            if collection is not None or (job_type is not None and new_job_type != task.job_type):
                collection_raw = (collection or None) if collection is not None else updated_payload.get("collection")
                try:
                    updated_payload = apply_collection(updated_payload, collection_raw, job_type=new_job_type)
                except CollectionIntentError as error:
                    return {"error": collection_error_message(str(error))}

            if task_plan is not None:
                from app.services.task_plans import apply_parsed_task_plan

                try:
                    updated_payload = apply_parsed_task_plan(updated_payload, parsed_plan, job_type=new_job_type)
                except ValueError as error:
                    return {"error": str(error)}
            elif (task.payload or {}).get("task_plan") is not None and existing_plan is None:
                from app.services.task_plans import apply_task_plan

                # Explicitly clear the plan block and its collection/date
                # projection when the task becomes a digest or another legacy type.
                updated_payload = apply_task_plan(updated_payload, None, job_type=new_job_type)

            # Compute next_run_at if cron changed
            tenant = await Tenant.objects.get(id=current_tenant_id(), session=session)
            updates: dict[str, Any] = {
                "job_type": new_job_type,
                "payload": updated_payload,
            }
            # A field the caller did not send keeps its current value. Writing
            # `agent_scenario_id: None` on every update silently unbound the task's
            # scenario — the analysis then ran without its methodology. The same
            # happened to the trial flag, which was set after the payload was written
            # and therefore never saved at all.
            if clearing_existing_plan:
                updates["agent_scenario_id"] = mutation.changes["agent_scenario_id"]
            elif clear_scenario:
                updates["agent_scenario_id"] = None
            elif scenario_id is not None:
                updates["agent_scenario_id"] = scenario_id
            elif task_plan is not None:
                # A complete plan replaces the relationship too, including an
                # explicit empty scenario choice.
                updates["agent_scenario_id"] = parsed_plan.scenario_id
            if cron is not None and (cron != task.cron_expr or task_plan is not None):
                updates["cron_expr"] = cron
                tz = resolve_tz(tenant)
                if task_plan is not None:
                    from app.services.task_plans import task_plan_timezone

                    tz = task_plan_timezone(task_plan, tz)
                updates["next_run_at"] = next_run_at(new_cron, tz)
            if deactivate_after_success is not None:
                # A trial task gets a live schedule back: it had been switched off
                # after its success, so re-arming means the next run must be planned.
                updates["is_active"] = True
                if cron is None or cron == task.cron_expr:
                    updates["next_run_at"] = next_run_at(new_cron, resolve_tz(tenant))

            await AgentTask.objects.update_by_id(task.id, session=session, **updates)
            if source_ids is not None:
                await AgentTaskManager().set_sources(task.id, source_ids, session=session)

            changes: dict[str, Any] = {
                "status": "updated", "id": task.id, "task_url": f"/app/tasks/{task.id}", "name": task.name
            }
            if job_type:
                changes["old_job_type"] = original_job_type
                changes["new_job_type"] = job_type
            if cron:
                changes["old_cron"] = original_cron
                changes["new_cron"] = cron
            if source_ids is not None:
                changes["sources_updated"] = True
            return changes
    except ValueError as error:
        return {"error": str(error)}
    finally:
        await session.close()


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


@tool(
    name="task_execute_once",
    description=(
        "Запустить одну ограниченную социальную задачу поиска и анализа сразу через общий диспетчер. "
        "При повторе того же запроса передай тот же request_id: результат запуска будет использован повторно. "
        "Разные намерения пользователя получают разные request_id. Не принимает произвольный payload или тип задачи."
    ),
    required_permission="scheduling.task.create",
    parameters={
        "type": "object",
        "properties": {
            "request_id": {
                "type": "string",
                "description": "Стабильный уникальный ключ этого намерения; сохрани его для повторной попытки",
            },
            "plan": {
                "type": "object",
                "description": (
                    "Утверждённый task_plan для разового запуска: version, goal, platforms, "
                    "source_scope, source_ids, collection, window, scenario_id, trigger={type: once}"
                ),
            },
        },
        "required": ["request_id", "plan"],
    },
)
async def task_execute_once(request_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Queue one bounded analyze run through the shared admission/deduplication service."""
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
        return {"error": "request_id должен содержать от 8 до 128 символов", "code": "request_id_invalid"}
    try:
        from app.services.task_launch import launch_social_task

        return await launch_social_task(plan, request_id=request_id, job_type="analyze")
    except (TypeError, ValueError) as error:
        return {"error": str(error), "code": "task_plan_invalid"}
    except PermissionError:
        return {"error": "Нет доступа к запуску задачи или личному плательщику", "code": "task_launch_forbidden"}
    except RuntimeError:
        return {
            "error": "Запуск недоступен: проверьте личный AI-бюджет и состояние источников на странице задачи",
            "code": "task_launch_unavailable",
        }


@tool(
    name="task_result",
    description=(
        "Показать состояние запуска и доступные результаты задачи. "
        "После task_execute_once используй возвращённый task_id; queued ещё не означает готовый результат."
    ),
    required_permission="scheduling.task.view",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "integer"},
            "job_id": {"type": "integer"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["task_id"],
    },
)
async def task_result(task_id: int, job_id: int | None = None, limit: int = 5) -> dict[str, Any]:
    from app.services.task_results import task_result as read_result

    try:
        result = await read_result(task_id, job_id=job_id, limit=limit)
    except ValueError as error:
        return {"error": str(error), "code": "task_result_invalid"}
    return result if result is not None else {"error": "Задача или запуск не найдены", "code": "task_not_found"}


@tool(
    name="search_plan",
    description=(
        "Подготовить ограниченный план социального поиска по запросу пользователя. "
        "Не запускает сбор и не создаёт источники; варианты остаются предложениями модели."
    ),
    required_permission="collection.source.view",
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "Что пользователь хочет найти и зачем"},
            "platform": {"type": "string", "enum": ["vk", "telegram"]},
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "До пяти предложенных поисковых формулировок; не утверждай, что они уже проверены API",
            },
            "source_scope": {"type": "string", "enum": ["selected", "discovery", "all"]},
            "source_ids": {"type": "array", "items": {"type": "integer"}},
            "city": {"type": "string", "description": "Необязательный географический фильтр"},
            "vk_access": {
                "type": "object",
                "description": "Необязательный закреплённый доступ: mode=api либо mode=user и credential_id",
            },
        },
        "required": ["goal", "platform", "source_scope"],
    },
)
async def search_plan(
    goal: str,
    platform: str,
    source_scope: str,
    queries: list[str] | None = None,
    source_ids: list[int] | None = None,
    city: str | None = None,
    vk_access: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and return a bounded search proposal without platform access."""
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 2_000:
        return {"error": "Опишите цель поиска (до 2000 символов)", "code": "goal_invalid"}
    if platform not in {"vk", "telegram"}:
        return {"error": "Пока доступны только VK и Telegram", "code": "platform_unsupported"}
    if source_scope not in {"selected", "discovery", "all"}:
        return {"error": "Укажите selected, discovery или all", "code": "source_scope_invalid"}
    ids = source_ids or []
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
        return {
            "error": "Идентификаторы источников должны быть положительными целыми числами",
            "code": "source_ids_invalid",
        }
    if source_scope == "selected" and not ids:
        return {"error": "Для selected укажите хотя бы один source_id", "code": "sources_required"}
    if source_scope != "selected" and ids:
        return {"error": "source_ids допустимы только для selected", "code": "source_ids_unexpected"}
    if queries is None:
        queries = []
    if not isinstance(queries, list) or len(queries) > 5 or any(
        not isinstance(query, str) or not query.strip() or len(query.strip()) > 200 for query in queries
    ):
        return {"error": "Укажите не более пяти непустых запросов длиной до 200 символов", "code": "queries_invalid"}
    if city is not None and (not isinstance(city, str) or not city.strip() or len(city.strip()) > 120):
        return {"error": "Некорректное название города", "code": "city_invalid"}
    if vk_access is not None:
        from app.services.social.vk_access import VKSelectionError, parse_vk_selection

        try:
            vk_access = parse_vk_selection(vk_access).to_dict()
        except (VKSelectionError, TypeError, AttributeError):
            return {"error": "Некорректный выбор подключения ВКонтакте", "code": "vk_access_selection_invalid"}
    from app.services.collection.entitlement_policy import preview_entitlements

    entitlement_preview = await preview_entitlements({
        "task_plan": {
            "platforms": [platform], "source_scope": source_scope, "source_ids": ids,
            "collection": {"queries": queries},
        },
        "collection": {"queries": queries},
    })
    from app.services.social.vk_task_access import preview_vk_access

    preview_plan = {
        "version": 1,
        "goal": goal.strip(),
        "platforms": [platform],
        "source_scope": source_scope,
        "source_ids": ids,
        "collection": {"queries": queries},
        "window": {"type": "rolling", "days": 1},
        "trigger": {"type": "once"},
    }
    if vk_access is not None:
        preview_plan["vk_access"] = vk_access
    vk_access_preview = await preview_vk_access(preview_plan)
    return {
        "entitlements": entitlement_preview,
        "vk_access": vk_access_preview,
        "status": "proposal",
        "goal": goal.strip(),
        "platform": platform,
        "queries": list(dict.fromkeys(query.strip() for query in queries)),
        "source_scope": source_scope,
        "source_ids": ids,
        "city": city.strip() if city else None,
        "execution": "not_started",
        "note": (
            "Формулировки предложены моделью; доступность и фактический охват "
            "подтверждаются только запуском через API."
        ),
    }


@tool(
    name="task_plan",
    description=(
        "Проверить и показать полный план социальной задачи без сохранения или запуска. "
        "Используй перед разовым поиском либо созданием расписания."
    ),
    required_permission="scheduling.task.create",
    parameters={
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "description": (
                    "version, goal, platforms, source_scope, source_ids, collection, "
                    "vk_access {mode: api|user, credential_id?}, "
                    "window {type: fixed|rolling, start, end, days}, scenario_id, "
                    "trigger {type: once|schedule, cron, timezone}"
                ),
            }
        },
        "required": ["plan"],
    },
)
async def task_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Pure validation/preview; the shared plan parser also serves other surfaces."""
    from app.services.task_plans import parse_task_plan, task_plan_block

    try:
        parsed = parse_task_plan(plan)
    except (TypeError, ValueError) as error:
        return {"error": str(error), "code": "task_plan_invalid"}
    # Verify workspace-owned references before returning a preview as usable.
    from app.models import AgentScenario, Source

    normalized = task_plan_block(parsed)
    source_ids = normalized.get("source_ids") or []
    if source_ids:
        found = await Source.objects.select_related("platform").filter(id__in=source_ids, is_active=True)
        found_ids = {source.id for source in found}
        missing = sorted(set(source_ids) - found_ids)
        if missing:
            return {"error": "Один или несколько источников не найдены или отключены", "code": "source_unavailable"}
        from app.utils.enum_helpers import get_enum_value

        platforms = {get_enum_value(getattr(source.platform, "platform_type", None)) for source in found}
        requested = set(normalized.get("platforms") or [])
        if not platforms.issubset(requested):
            return {"error": "Платформа выбранного источника не входит в план", "code": "source_platform_mismatch"}
    scenario_id = normalized.get("scenario_id")
    if scenario_id is not None:
        scenario = await AgentScenario.objects.get(id=scenario_id)
        if scenario is None or not scenario.is_active:
            return {"error": "Сценарий не найден или отключён в этом workspace", "code": "scenario_unavailable"}
    from app.services.collection.entitlement_policy import preview_entitlements

    entitlements = await preview_entitlements(
        {"task_plan": normalized, "collection": normalized.get("collection", {})}
    )
    from app.services.social.vk_task_access import preview_vk_access

    vk_access_preview = await preview_vk_access(normalized)
    return {
        "status": "validated_preview", "plan": normalized, "writes": False,
        "run_created": False, "entitlements": entitlements, "vk_access": vk_access_preview,
    }
