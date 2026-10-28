"""Operations SQLAdmin views."""

from __future__ import annotations

from ._shared import (
    logging,
    ActionType,
    AgentScenario,
    AgentTask,
    Any,
    BaseAdmin,
    BotAction,
    BotActionStatus,
    FilteredSelectMultipleField,
    HTTPException,
    Job,
    JobType,
    RedirectResponse,
    Request,
    Select,
    SelectField,
    Source,
    Tenant,
    action,
    cron_to_human,
    datetime,
    expose,
    logger,
    timedelta,
    timezone,
    validators,
)


logger = logging.getLogger(__name__)


class BotActionAdmin(BaseAdmin, model=BotAction):
    identity = "bot-action"
    can_edit = False
    can_delete = False
    """
    Read-only capability-neutral proposal/audit ledger. action_send only previews.
    Approval/cancel transitions must go through the actor-bound manager.
    """

    name = "Действие бота"
    name_plural = "Действия ботов"
    icon = "fa fa-bolt"
    can_create = False
    form_columns = []

    inline_columns = ["id", "action_type", "status", "dry_run", "created_at"]
    column_list = ["id", "agent_scenario_id", "source_id", "action_type", "status", "dry_run", "created_at"]
    column_searchable_list = ["status", "action_type"]
    column_sortable_list = ["created_at", "status"]
    column_default_sort = [("created_at", True)]

    column_labels = dict(
        {
            "id": "ID",
            "agent_scenario_id": "Сценарий",
            "source_id": "Источник",
            "analytics_id": "Аналитика",
            "action_type": "Тип действия",
            "status": "Статус",
            "payload": "Данные действия",
            "result": "Результат",
            "error": "Ошибка",
            "dry_run": "Тестовый запуск",
            "confirmed_by": "Подтверждено пользователем",
            "confirmed_at": "Время подтверждения",
            "attempts": "Попытки",
        },
        **BaseAdmin.column_labels,
    )

    form_widget_args = {
        "status": {"readonly": False},
    }

    form_overrides = {
        # SelectField override keeps the choices from form_args below (the default
        # enum converter would replace them with raw enum names)
        "status": SelectField,
        **BaseAdmin.form_overrides,
    }

    form_args = {
        "status": {
            "label": "Статус",
            "description": "Можно поправить вручную, например отменить зависшую запись PENDING",
            "choices": BotActionStatus.choices(),
            "coerce": str,
            "validators": [validators.AnyOf([value for value, _ in BotActionStatus.choices()])],
        },
        **BaseAdmin.form_args,
    }

    column_formatters = {
        "dry_run": lambda m, a: "✅ Да" if m.dry_run else "❌ Нет",
        "action_type": lambda m, a: m.action_type.label if m.action_type is not None else "—",
        "status": lambda m, a: m.status.label if m.status is not None else "—",
        **BaseAdmin.column_formatters,
    }

    column_formatters_detail = {
        "action_type": lambda m, a: m.action_type.label if m.action_type is not None else "—",
        "status": lambda m, a: m.status.label if m.status is not None else "—",
    }


class AgentTaskAdmin(BaseAdmin, model=AgentTask):
    @expose("/vk-connections", methods=["GET"])
    async def vk_connections(self, request: Request):
        """Metadata for the signed-in operator's own workspace connection only."""
        from starlette.responses import JSONResponse

        from app.core.permissions import permission_scope
        from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
        from app.services.social.vk_connections import connection_catalog

        actor = self.get_admin_user(request)
        try:
            tenant_id = int(request.query_params.get("tenant_id", ""))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="workspace_required") from None
        if not is_bypass() and current_tenant_id() != tenant_id:
            raise HTTPException(status_code=403, detail="workspace_selection_mismatch")
        if actor is None or type(getattr(actor, "id", None)) is not int:
            raise HTTPException(status_code=403, detail="workspace_actor_required")
        try:
            with tenant_scope(tenant_id), permission_scope(actor):
                catalog = await connection_catalog(tenant_id=tenant_id, actor_id=actor.id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="vk_access_unavailable") from None
        return JSONResponse(catalog)

    name = "Задача"
    name_plural = "Задачи"
    icon = "fa fa-clock"

    # Запуск задачи ставит job в очередь — это изменение, а не просмотр.
    action_permissions = {"run-now": ActionType.UPDATE}

    inline_columns = ["id", "name", "job_type", "is_active", "next_run_at", "last_status"]
    column_list = [
        "id",
        "tenant",
        "name",
        "job_type",
        "cron_expr",
        "is_active",
        "dry_run",
        "next_run_at",
        "last_run_at",
        "last_status",
    ]
    column_searchable_list = ["name", "job_type"]
    column_sortable_list = ["name", "job_type", "is_active", "next_run_at"]
    column_labels = dict(
        {
            "id": "ID",
            "tenant": "Рабочее пространство",
            "name": "Название",
            "job_type": "Тип",
            "cron_expr": "Расписание",
            "payload": "Параметры",
            "is_active": "Активна",
            "next_run_at": "Следующий запуск",
            "last_run_at": "Последний запуск",
            "last_status": "Статус",
            "last_error": "Ошибка",
            "sources": "Источники",
            "agent_scenario": "Сценарий агента",
            "trigger_type": "Тип триггера",
            "trigger_config": "Настройки триггера",
            "action_type": "Действие после анализа",
            "rate_limit_per_hour": "Лимит действий в час",
            "cooldown_seconds": "Пауза между действиями (сек)",
            "dry_run": "Тестовый прогон (dry-run)",
        },
        **BaseAdmin.column_labels,
    )

    form_columns = [
        "tenant",
        "name",
        "job_type",
        "cron_expr",
        "sources",
        "agent_scenario",
        "payload",
        "is_active",
        # Trigger/action configuration lives here, not on the scenario: it
        # describes what this run does with its own analyses.
        "trigger_type",
        "trigger_config",
        "action_type",
        "rate_limit_per_hour",
        "cooldown_seconds",
        "dry_run",
    ]

    form_overrides = {
        "sources": FilteredSelectMultipleField,
        "is_active": SelectField,
        "job_type": SelectField,
        **BaseAdmin.form_overrides,
    }

    form_widget_args = {
        "name": {"placeholder": "Например: hourly-collect"},
        "cron_expr": {"placeholder": "0 * * * *"},
        "payload": {"rows": 8, "placeholder": '{"period": "day", "collection": {"content_types": ["posts"]}}'},
        "trigger_config": {
            "rows": 5,
            "placeholder": '{\n  "keywords": ["жалоба", "проблема"],\n  "mode": "any"\n}',
        },
    }

    form_args = {
        "tenant": {
            "label": "Рабочее пространство",
            "description": "Владелец задачи: выберите Ваше рабочее пространство из списка",
        },
        "name": {
            "label": "Название",
            "description": "Уникальное системное имя задачи, например daily-digest или hourly-collect",
        },
        "job_type": {
            "label": "Тип задачи",
            "description": "Вид операции: сбор данных, отправка дайджеста, анализ или рефлексия",
            "choices": JobType.choices(),
            "coerce": str,
        },
        "cron_expr": {
            "label": "Cron-выражение",
            "description": "Расписание из 5 полей: минута час день месяц день-недели (например, '0 * * * *')",
        },
        "sources": {
            "label": "Источники",
            "description": "Источники, над которыми работает задача. Пусто — все активные источники",
        },
        "agent_scenario": {
            "label": "Сценарий бота",
            "description": "Сценарий анализа и реакции; пусто — используется сценарий по умолчанию",
        },
        "payload": {
            "label": "Параметры",
            "description": (
                'JSON задачи, например {"period": "day"}. VK: task_plan.vk_access = '
                '{"mode":"api"} либо {"mode":"user","credential_id":ID}. '
                "Режим, аккаунт, scopes, срок и матрица методов: "
                "/admin/agent-task/vk-connections?tenant_id=ID (только свои подключения). "
                "Сбор (job_type=analyze): collection — "
                "content_types (posts|comments), engagement_types (likes|reposts), limit, queries, "
                "city {name, mode}, discovery {people, communities, queries}, comments "
                "{max_per_post, total}, candidates {limit, confirm}, date_window {start, end}. "
                "Выборка (docs/COLLECTION.md#task-selection-contract): tracked_users — авторы из источников задачи, "
                "чьи записи анализируются (пусто — записи самих источников); keywords_list + "
                "keywords_match (any|all) — фразы, которые должны быть в записи. Что и где собирать, "
                "задаёт задача; сценарий отвечает за методику анализа"
            ),
        },
        "dry_run": {
            "label": "Тестовый прогон (dry-run)",
            "description": "Задача отрабатывает без записи собранных данных и без вызова модели",
        },
        "is_active": {
            "label": "Активна",
            "choices": [(True, "Да"), (False, "Нет")],
            "coerce": lambda x: x == "True" if isinstance(x, str) else bool(x),
            "description": "Выключенная задача не запускается планировщиком",
        },
        **BaseAdmin.form_args,
    }

    column_formatters = {
        # next_run_at / last_run_at inherit the shared DD.MM.YYYY HH:MM formatter
        "last_status": lambda m, a: m.last_status or "—",
        "cron_expr": lambda m, a: cron_to_human(m.cron_expr),
        "dry_run": lambda m, a: "Да" if m.dry_run else "Нет",
        "sources": lambda m, a: ", ".join(s.name for s in m.sources) or "Все активные",
        "agent_scenario": lambda m, a: m.agent_scenario.name if m.agent_scenario is not None else "—",
        **BaseAdmin.column_formatters,
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
        """Schedule a task saved through the admin form.

        The admin form omits `next_run_at`, so a task created/edited here would
        otherwise never become due. Compute it from the cron expression so
        admin-created tasks actually fire (a @once task is scheduled ~1 min out).
        """
        # The payload is a JSON column, so the form can hand back any JSON value
        # (a list, a string, a number). The collection limits inside it are
        # parsed by the shared writer on every other write surface (web, API,
        # CLI, chat); the admin used to store them unchecked, so a malformed
        # limit reached the runner. Validate and canonicalise before any query.
        from collections.abc import Mapping

        from app.services.collection.intent import CollectionIntentError, apply_collection, collection_error_message

        job_type = data.get("job_type") or getattr(model, "job_type", "") or ""
        raw_payload = data["payload"] if "payload" in data else getattr(model, "payload", None)
        if raw_payload is None:
            raw_payload = {}
        if not isinstance(raw_payload, Mapping):
            raise ValueError('Параметры задачи: нужен JSON-объект, например {"period": "day"}')
        try:
            payload = apply_collection(dict(raw_payload), raw_payload.get("collection"), job_type=job_type)
            if "task_plan" in raw_payload:
                from app.services.task_plans import TaskPlanError, apply_task_plan, parse_task_plan

                plan_raw = raw_payload["task_plan"]
                if is_created:
                    from app.services.task_creation import resolve_task_plan_create

                    creation = resolve_task_plan_create(plan_raw)
                    plan = creation.plan
                else:
                    plan = parse_task_plan(plan_raw)
                planned_cron = "@once" if plan.trigger.type == "once" else plan.trigger.cron
                cron_value = data.get("cron_expr") or getattr(model, "cron_expr", None)
                if cron_value != planned_cron:
                    raise TaskPlanError("cron_expr must match task_plan.trigger")
                raw_sources = data.get("sources")
                if plan.source_scope == "selected" and raw_sources is not None:
                    linked_ids = sorted(getattr(source, "id", source) for source in raw_sources)
                    if linked_ids != sorted(plan.source_ids):
                        raise TaskPlanError("Admin source selection must match task_plan.source_scope")
                raw_scenario = data.get("agent_scenario")
                selected_scenario = getattr(raw_scenario, "id", raw_scenario)
                if selected_scenario is not None and selected_scenario != plan.scenario_id:
                    raise TaskPlanError("Admin scenario must match task_plan.scenario_id")
                if plan.source_scope != "selected":
                    data["sources"] = []
                if is_created:
                    from app.services.task_creation import project_task_plan_create

                    payload = project_task_plan_create(payload, creation, job_type=job_type).payload
                else:
                    payload = apply_task_plan(payload, plan_raw, job_type=job_type)
            from app.services.task_plans import validate_vk_access_retained

            validate_vk_access_retained(getattr(model, "payload", None), payload, job_type=job_type)
            data["payload"] = payload
        except CollectionIntentError as exc:
            raise ValueError(collection_error_message(str(exc))) from None
        except ValueError as exc:
            raise ValueError(str(exc)) from None

        # Guard: an active source-based task (collect/analyze) needs at least one
        # active source to operate on. Empty links fall back to "all active
        # sources", so the check is on the workspace, mirroring the web rule.
        from app.core.tenant_context import tenant_scope
        from app.models import Source
        from app.models.managers.agent_task_manager import AgentTaskManager

        active = data.get("is_active", getattr(model, "is_active", False))
        # The platform admin has no personal payer context. A newly authored
        # paid plan must be activated by its workspace owner after consent.
        if is_created and isinstance(data.get("payload"), Mapping) and "task_plan" in data["payload"]:
            active = False
            data["is_active"] = False
            if request is not None and getattr(request, "session", None) is not None:
                request.session["admin_message"] = {
                    "type": "warning",
                    "message": "Новая задача сохранена неактивной: владелец пространства должен настроить оплату и активировать её.",
                }
        tenant_val = data.get("tenant") or getattr(model, "tenant_id", None)
        tenant_id = tenant_val.id if hasattr(tenant_val, "id") else tenant_val
        # The form delivers the workspace as a string ("1"), and a `tenant_id`
        # compared against `sources.tenant_id` as VARCHAR fails with
        # `operator does not exist: integer = character varying`. Coerce once,
        # here, rather than trusting every caller to pass an int.
        if tenant_id is not None:
            try:
                tenant_id = int(tenant_id)
            except (TypeError, ValueError):
                tenant_id = None
        if active and AgentTaskManager.requires_sources(job_type):
            submitted = data.get("sources") or []
            submitted_ids = [int(v) if isinstance(v, (int, str)) else getattr(v, "id", None) for v in submitted]
            submitted_ids = [i for i in submitted_ids if i is not None]
            with tenant_scope(bypass=True):
                if submitted_ids:
                    has_active = (
                        await Source.objects.filter(Source.id.in_(submitted_ids), tenant_id=tenant_id, is_active=True)
                        .values(Source.id)
                        .rows()
                    )
                else:
                    has_active = (
                        await Source.objects.filter(tenant_id=tenant_id, is_active=True).values(Source.id).rows()
                    )
            if not has_active:
                raise ValueError(
                    "Задача типа «сбор данных»/«анализ» не может быть активной без активного источника: "
                    "выберите хотя бы один источник или снимите флаг «Активна»"
                )

        # Guard: missing target params for the scenario's analysis types.
        # Non-blocking warning: a task is still valid without them, but analysis
        # will have nothing specific to look for.
        scenario_id_val = data.get("agent_scenario") or getattr(model, "agent_scenario_id", None)
        if scenario_id_val is not None:
            scenario_pk = (
                int(scenario_id_val)
                if isinstance(scenario_id_val, (int, str))
                else getattr(scenario_id_val, "id", None)
            )
            if scenario_pk and request is not None:
                with tenant_scope(bypass=True):
                    scenario = await AgentScenario.objects.filter(id=scenario_pk).first()
                if scenario and scenario.analysis_types:
                    from app.services.ai.param_registry import missing_target_params

                    task_payload = data.get("payload") or {}
                    missing = missing_target_params(scenario.analysis_types, task_payload)
                    if missing:
                        msg = (
                            "⚠️ Для сценария «"
                            + (scenario.name or str(scenario_pk))
                            + "» укажите в параметрах задачи: "
                            + ", ".join(missing)
                        )
                        # Starlette's Session is dict-like and has no `flash()`
                        # (that is Flask) — the save used to die here with
                        # AttributeError. The admin layout renders this key as a toast.
                        session = getattr(request, "session", None)
                        if session is not None:
                            session["admin_message"] = {"type": "warning", "message": msg}

        cron_expr = (data.get("cron_expr") or getattr(model, "cron_expr", "") or "").strip()
        if cron_expr and getattr(model, "next_run_at", None) is None:
            if cron_expr == "@once":
                data["next_run_at"] = datetime.now(timezone.utc) + timedelta(minutes=1)
            else:
                from app.models import Tenant
                from app.tasks.cron import next_run_at as compute_next
                from app.tasks.cron import resolve_tz

                # The workspace's own zone, same rule as the web form and the
                # runner; a task scheduled here in the global zone would move by
                # the offset between the two on its first fire.
                with tenant_scope(bypass=True):
                    tenant = await Tenant.objects.get(id=tenant_id) if tenant_id else None
                from app.services.task_plans import task_plan_timezone

                task_plan = (data.get("payload") or {}).get("task_plan")
                data["next_run_at"] = compute_next(
                    cron_expr,
                    task_plan_timezone(task_plan, resolve_tz(tenant)),
                )
        await super().on_model_change(data, model, is_created, request)

    def list_query(self, request: Request) -> Select:
        return AgentTask.objects.prefetch_related("sources", "agent_scenario").filter().to_select()

    def details_query(self, request: Request) -> Select:
        pk = int(request.path_params["pk"])
        return AgentTask.objects.prefetch_related("sources", "agent_scenario").filter(id=pk).to_select()

    @action(
        name="run_now",
        label="Выполнить сейчас",
        add_in_list=True,
        add_in_detail=True,
    )
    async def run_now_action(self, request: Request):
        """Run the selected task's job immediately; completes a @once task."""
        from app.core.tenant_context import current_tenant_id, tenant_scope
        from app.jobs.dispatcher import run_task_directly

        pks = request.query_params.get("pks", "")
        if not pks:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=303)
        task_id = int(pks.split(",")[0])

        # The manual Job admission boundary (`_admit_task_run`) credits the
        # actor from the ambient permission_scope user into the Job row
        # (`initiated_by_user_id`). That identity comes only from the session
        # the admin authorization backend put on `request.state.admin_user` —
        # never from query/path/body or the task payload. Fail closed when the
        # trusted operator is absent or inactive.
        admin_user = self.get_admin_user(request)
        if admin_user is None or not getattr(admin_user, "is_active", False):
            raise HTTPException(status_code=403, detail="Доступ запрещён: оператор не аутентифицирован")

        # Read only inside the actor's selected workspace. Preserve its id;
        # opening a tenant-less scope would either leak rows via bypass or fail
        # closed for every ordinary operator. Only the owner retains bypass.
        with tenant_scope(current_tenant_id(), bypass=bool(getattr(admin_user, "is_superuser", False))):
            task = await AgentTask.objects.get(id=task_id)
        if task:
            from app.core.permissions import permission_scope

            with permission_scope(admin_user):
                outcome = await run_task_directly(task)
            if outcome and outcome.get("status") == "failed":
                request.session["admin_message"] = {
                    "type": "error",
                    "message": f"Задача «{task.name}» не выполнена: {outcome.get('error') or '?'}",
                }
            else:
                request.session["admin_message"] = {"type": "success", "message": f"Задача «{task.name}» выполнена"}
        return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=303)


class JobAdmin(BaseAdmin, model=Job):
    """Global operator inspection; mutations use the scoped queue workflow."""

    name = "Выполнение"
    name_plural = "Очередь выполнения"
    icon = "fa fa-list"
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    column_labels = {
        **BaseAdmin.column_labels,
        "job_type": "Тип задания",
        "status": "Статус",
        "run_at": "Запуск",
        "attempts": "Попытки",
    }
    inline_columns = ["id", "job_type", "status", "run_at", "attempts"]
    column_list = ["id", "tenant_id", "agent_task_id", "job_type", "status", "run_at", "attempts"]
    column_details_list = column_list + ["locked_at", "started_at", "finished_at", "created_at", "updated_at"]
    column_sortable_list = ["id", "run_at", "status"]
    column_searchable_list = ["job_type", "status"]

__all__ = [name for name in globals() if not name.startswith("__")]
