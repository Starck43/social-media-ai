"""HTTP route for creating a task and its initial source bindings."""

from __future__ import annotations

from .task_shared import (
    AgentScenario,
    AgentTask,
    AgentTaskManager,
    Form,
    RedirectResponse,
    Request,
    action_tenant_id,
    add_flash,
    datetime,
    ensure_csrf,
    guard_web,
    router,
    safe_next,
    timedelta,
    timezone,
)

from .task_forms import (
    AgentScenario,
    AgentTask,
    AgentTaskManager,
    RedirectResponse,
    Request,
    _activate_selected_sources,
    _apply_targets,
    _can_activate,
    _can_manage_job_retention,
    _check_task_sources,
    _collection_block,
    _duplicate_name,
    _normalize_web_job_type,
    _payload_from_form,
    _split_phrases,
    _task_plan_from_form,
    _trigger_and_action_fields,
    _unified_analysis_scenario_block,
    add_flash,
    apply_selection,
)

from .task_use_cases import (
    AgentTaskManager,
    _can_activate,
    queue_task_now,
)

from .task_presenters import (
    AgentTaskManager,
    Request,
    datetime,
    timedelta,
    timezone,
)


@router.post("")
async def task_create(
    request: Request,
    name: str = Form(...),
    job_type: str = Form(...),
    cron_custom: str = Form(...),
    source_ids: list[str] = Form([]),
    source_scope: str = Form("legacy"),
    task_goal: str = Form(""),
    platforms: list[str] = Form([]),
    vk_access: str = Form(""),
    window_type: str = Form("fixed"),
    rolling_days: int | None = Form(default=None),
    trigger_timezone: str = Form(""),
    scenario_id: int = Form(default=None),
    tracked_users: str = Form(""),
    keywords_match: str = Form("any"),
    brands: str = Form(""),
    competitors: str = Form(""),
    hashtags: str = Form(""),
    influencer_names: str = Form(""),
    topic_list: str = Form(""),
    keywords_list: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    digest_period: str = Form(""),
    digest_group_by: str = Form(""),
    digest_time_breakdown: str = Form(""),
    compose_digest: str = Form(""),
    digest_window_type: str = Form("rolling"),
    digest_rolling_days: int = Form(7),
    digest_start_date: str = Form(""),
    digest_end_date: str = Form(""),
    include_collection: str = Form(""),
    retention_success_hours: int = Form(24),
    retention_failure_hours: int = Form(24),
    trigger_type: str = Form(""),
    trigger_keywords: str = Form(""),
    trigger_match: str = Form("any"),
    trigger_usernames: str = Form(""),
    trigger_threshold: str = Form(""),
    trigger_direction: str = Form("below"),
    trigger_baseline_hours: str = Form(""),
    trigger_spike_multiplier: str = Form(""),
    action_type: str = Form(""),
    rate_limit_per_hour: str = Form(""),
    cooldown_seconds: str = Form(""),
    dry_run: str = Form(""),
    run_now: str = Form(""),
    collection_configured: str = Form(""),
    collection_people: str = Form(""),
    collection_communities: str = Form(""),
    collection_discovery_queries: str = Form(""),
    collection_discovery_queries_match: str = Form("any"),
    collection_discovery_city: str = Form(""),
    collection_discovery_city_id: str = Form(""),
    collection_queries: str = Form(""),
    collection_queries_match: str = Form("any"),
    collection_content_types: list[str] = Form([]),
    collection_engagement_types: list[str] = Form([]),
    collection_limit: str = Form(""),
    collection_comments_max_per_post: str = Form(""),
    collection_comments_total: str = Form(""),
    collection_candidates_limit: str = Form(""),
    collection_candidates_confirm: str = Form("manual"),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
    # Where to land after creating — the onboarding wizard posts here too, and
    # every failure below has to come back to the page that was filled in.
    next: str = Form(""),
):
    back = safe_next(next) or "/app/tasks"
    # `None` when the caller stayed on /app/tasks; the run-status modal only
    # exists there, so an external caller (onboarding) takes the plain flash.
    return_to = safe_next(next)

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)
    job_type = _normalize_web_job_type(job_type)
    if job_type == "prune" and not _can_manage_job_retention(request):
        add_flash(
            request, "error", "Настраивать очистку истории заданий могут только администратор и владелец пространства"
        )
        return RedirectResponse(back, status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "agenttask", "create", back=back)
    if denied is not None:
        return denied

    # A rehearsal is an operator diagnostic: only an administrator or owner may
    # put a task into it, and the toggle never silently applies to a task the
    # person cannot manage.
    dry_run_flag = dry_run == "on"
    if dry_run_flag and not _can_manage_job_retention(request):
        add_flash(request, "warning", "Тестовый прогон доступен только администратору и владельцу пространства")
        dry_run_flag = False

    cron_expr = cron_custom.strip()

    # Before anything is written: a repeat of the same form (a second click on
    # "Создать и выполнить" while the first is still running) must not reach the
    # unique constraint as an IntegrityError.
    clash = await _duplicate_name(request, back, name.strip()[:100])
    if clash is not None:
        return clash

    from app.models.managers.agent_task_manager import AgentTaskManager

    tasks_mgr = AgentTaskManager()
    if not tasks_mgr.validate_cron(cron_expr):
        add_flash(request, "error", f"Некорректное cron-выражение: {cron_expr}")
        return RedirectResponse(back, status_code=302)
    if job_type == "prune" and cron_expr == "@once":
        add_flash(request, "error", "Настройка хранения истории должна быть повторяемой, например ежедневно.")
        return RedirectResponse(back, status_code=302)

    parsed_source_ids: list[int] = []
    if AgentTaskManager.requires_sources(job_type):
        for s in source_ids:
            s = s.strip()
            if s.isdigit():
                parsed_source_ids.append(int(s))

    try:
        payload = _payload_from_form(
            job_type,
            start_date,
            end_date,
            digest_period=digest_period,
            digest_group_by=digest_group_by,
            digest_time_breakdown=digest_time_breakdown == "on",
            retention_success_hours=retention_success_hours,
            retention_failure_hours=retention_failure_hours,
            window_type=window_type,
        )
        if job_type != "prune":
            apply_selection(payload, tracked_users, _split_phrases(keywords_list), keywords_match)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    if job_type != "prune":
        _apply_targets(
            payload,
            {
                "brands": brands,
                "competitors": competitors,
                "hashtags": hashtags,
                "influencer_names": influencer_names,
                "topic_list": topic_list,
            },
        )

    # Collection intent is written only when the submitted form carried it: an
    # older client that posts the legacy fields alone keeps the task's stored
    # block instead of silently resetting it to posts.
    if collection_configured == "1":
        if job_type in ("collect", "analyze") and window_type == "rolling":
            payload.pop("cli_dates", None)
        try:
            collection_block = _collection_block(
                job_type,
                people=collection_people,
                communities=collection_communities,
                discovery_queries=collection_discovery_queries,
                discovery_queries_match=collection_discovery_queries_match,
                discovery_city=collection_discovery_city,
                discovery_city_id=collection_discovery_city_id,
                queries=collection_queries,
                queries_match=collection_queries_match,
                content_types=collection_content_types,
                engagement_types=collection_engagement_types,
                limit=collection_limit,
                comments_max_per_post=collection_comments_max_per_post,
                comments_total=collection_comments_total,
                candidates_limit=collection_candidates_limit,
                candidates_confirm=collection_candidates_confirm,
                date_window=payload.get("cli_dates"),
            )
        except ValueError as e:
            add_flash(request, "error", str(e))
            return RedirectResponse(back, status_code=302)
        if collection_block:
            payload["collection"] = collection_block
        else:
            payload.pop("collection", None)

    task_plan = None
    if job_type in ("collect", "analyze"):
        try:
            task_plan = _task_plan_from_form(
                task_goal,
                platforms,
                source_scope,
                parsed_source_ids,
                scenario_id,
                job_type,
                cron_expr,
                payload,
                window_type,
                rolling_days,
                trigger_timezone,
                vk_access,
                compose_digest=compose_digest == "true",
                digest_window_type=digest_window_type,
                digest_rolling_days=digest_rolling_days,
                digest_start_date=digest_start_date,
                digest_end_date=digest_end_date,
                digest_group_by=digest_group_by or "themes",
                digest_time_breakdown=digest_time_breakdown == "true",
                include_collection=include_collection == "true",
            )
            if task_plan is not None:
                from app.services.task_creation import prepare_task_plan_create

                prepared = prepare_task_plan_create(payload, task_plan, job_type=job_type)
                payload = prepared.payload
                cron_expr = prepared.cron_expr
                scenario_id = prepared.scenario_id
                parsed_source_ids = list(prepared.source_ids)
        except ValueError as error:
            add_flash(request, "error", f"Проверьте план задачи: {error}")
            return RedirectResponse(back, status_code=302)

    if job_type == "analyze":
        # A deactivated scenario may be chosen on purpose (it is on the task's
        # list and can be reactivated later), so the task is created and starts
        # inactive with the reason — the same contract the edit path has.
        scenario_block = await _unified_analysis_scenario_block(tenant_id, scenario_id, allow_inactive_selected=True)
        if scenario_block is not None:
            add_flash(request, "error", f"Для анализа соцсетей {scenario_block}")
            return RedirectResponse(back, status_code=302)

    # Warn about missing target params for the scenario's analysis types.
    # Non-blocking: the task is still created, but analysis will have nothing
    # specific to look for without these.
    if scenario_id and job_type != "prune":
        sc = await AgentScenario.objects.filter(id=scenario_id).first()
        if sc and sc.analysis_types:
            from app.services.ai.param_registry import missing_target_params

            if job_type == "analyze":
                missing = missing_target_params(sc.analysis_types, payload)
            else:
                missing = []
            if missing:
                add_flash(
                    request,
                    "warning",
                    f"⚠️ Для сценария «{sc.name}» укажите в параметрах задачи: " + ", ".join(missing),
                )

    trigger_action_fields = _trigger_and_action_fields(
        job_type,
        trigger_type,
        trigger_keywords,
        trigger_match,
        trigger_usernames,
        trigger_threshold,
        trigger_direction,
        trigger_baseline_hours,
        trigger_spike_multiplier,
        action_type,
        rate_limit_per_hour,
        cooldown_seconds,
    )

    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenants
    from app.tasks.cron import next_run_at, resolve_tz

    # The workspace zone wins; the global setting is only the fallback. Must
    # agree with the runner, which re-advances the schedule in the same zone.
    tenant = await tenants.get(id=tenant_id)
    from app.services.task_plans import task_plan_timezone

    tz = task_plan_timezone(task_plan, resolve_tz(tenant))

    if cron_expr == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(cron_expr, tz)

    try:
        await _check_task_sources(parsed_source_ids, tenant_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    # A ticked-but-off source activates with the save: the operator named the
    # source the task should run on, so it must not stay off and block the
    # activation check right below.
    if AgentTaskManager.requires_sources(job_type):
        await _activate_selected_sources(tenant_id, parsed_source_ids)

    # A task can only be created active when it has an active scenario and, for
    # a source-based type, at least one active source; otherwise it starts
    # inactive.
    activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id, payload=payload)
    if activation_blocked is not None:
        add_flash(
            request,
            "warning",
            f"Задача «{name}» создана неактивной: {activation_blocked}",
        )
    is_active = activation_blocked is None
    # New universal plans are saved as drafts until the creator establishes
    # payer consent. Existing non-plan tasks keep their prior activation path.
    needs_payer_consent = task_plan is not None and is_active

    try:
        with tenant_scope(tenant_id):
            from app.services.task_creation import create_task_with_sources

            task = await create_task_with_sources(
                task_values={
                    "name": name.strip()[:100],
                    "job_type": job_type,
                    "cron_expr": cron_expr,
                    "payload": payload,
                    "agent_scenario_id": None if job_type == "prune" else scenario_id,
                    "is_active": False if needs_payer_consent else is_active,
                    "next_run_at": next_run,
                    "dry_run": dry_run_flag,
                    **trigger_action_fields,
                },
                source_ids=parsed_source_ids if parsed_source_ids else None,
                tenant_id=tenant_id,
            )
    except ValueError:
        # A source that vanished between the check and this write must not leave
        # an active task whose empty link set means "all active sources".
        add_flash(
            request,
            "error",
            "Источники задачи изменились во время сохранения — обновите страницу и повторите",
        )
        return RedirectResponse(back, status_code=302)

    if needs_payer_consent:
        from app.models.managers.personal_billing_manager import PersonalBillingError, PersonalBillingUnavailable
        from app.services.billing import consent_current_user_as_task_payer

        try:
            await consent_current_user_as_task_payer(task.id)
        except (PersonalBillingError, PersonalBillingUnavailable):
            add_flash(
                request,
                "warning",
                f"Задача «{name}» сохранена неактивной. Настройте личный AI-бюджет и подтвердите оплату для активации.",
            )
            return RedirectResponse(back, status_code=302)
        await AgentTask.objects.update_by_id(task.id, is_active=True)
        task = await AgentTask.objects.get(id=task.id, tenant_id=tenant_id)

    # "Создать и выполнить" only queues the job. It used to run the handler
    # inside this request, which for a long job (an `analyze` task draining a
    # backlog) meant minutes of a frozen page — and a second click, which then
    # collided with the row the first click had written. The run-status modal
    # follows the queued job instead.
    if run_now:
        outcome = await queue_task_now(task, getattr(request.state.web_user, "id", None))
        if outcome.get("already_running"):
            add_flash(request, "info", f"Задача «{name}» создана, но источник уже занят. Новый запуск не создан")
        else:
            add_flash(request, "success", f"Задача «{name}» создана и поставлена в очередь на выполнение")
        # The run-status modal lives on the tasks page; from onboarding there is
        # nothing to poll, so land on the caller's page with the flash instead.
        job_id = (outcome or {}).get("job_id")
        target = f"/app/tasks?job_id={job_id}" if job_id else "/app/tasks"
        return RedirectResponse(back if return_to else target, status_code=302)
    else:
        add_flash(request, "success", f"Задача «{name}» создана")
    return RedirectResponse(back, status_code=302)

__all__ = [name for name in globals() if not name.startswith("__")]
