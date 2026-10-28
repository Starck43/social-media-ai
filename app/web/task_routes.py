"""HTTP routes for task list, create, detail, edit, and actions."""

from __future__ import annotations

from .task_forms import (
    TARGET_FIELD_DEFS,
    _activate_selected_sources,
    _apply_targets,
    _can_activate,
    _can_manage_job_retention,
    _check_task_sources,
    _collection_block,
    _collection_edit,
    _duplicate_name,
    _edit_payload,
    _group_sources,
    _normalize_web_job_type,
    _payload_from_form,
    _replace_task_sources,
    _split_phrases,
    _task_plan_from_form,
    _trigger_and_action_fields,
    _unified_analysis_scenario_block,
    _web_job_type_choices,
    apply_selection,
)
from .task_presenters import (
    JOB_TYPE_TITLES,
    _attach_task_lane_evidence,
    _attach_task_llm_diagnostics,
    _run_outcome,
    _task_run_panel_data,
    _task_run_scope,
    _task_source_ids,
)
from .task_shared import (
    AgentActionType,
    AgentScenario,
    AgentTask,
    AgentTaskManager,
    Any,
    BotTriggerType,
    Form,
    Job,
    RedirectResponse,
    Request,
    Response,
    Source,
    action_tenant_id,
    add_flash,
    cron_to_human,
    datetime,
    ensure_csrf,
    guard_web,
    outcome_unconfirmed,
    perms_can,
    render,
    router,
    safe_next,
    tenant_filter_context,
    timedelta,
    timezone,
)
from .task_use_cases import (
    queue_task_now,
)


@router.get("/vk-connections")
async def web_vk_connections(request: Request):
    """Return safe VK connection options for the authenticated task editor."""
    denied = guard_web(request, "agenttask", "view", back="/app/tasks")
    if denied is not None:
        return denied
    from app.core.permissions import get_current_user
    from app.services.social.vk_connections import connection_catalog

    actor = get_current_user()
    tenant_id = action_tenant_id(request)
    actor_id = getattr(actor, "id", None)
    if type(tenant_id) is not int or type(actor_id) is not int:
        return Response(status_code=403)
    try:
        return await connection_catalog(tenant_id=tenant_id, actor_id=actor_id)
    except PermissionError:
        return Response(status_code=403, content="vk_access_unavailable")


@router.get("")
@router.get("/")
async def tasks_list(request: Request):
    tenant_id = request.state.tenant_id
    user = getattr(request.state, "web_user", None)
    is_superuser = False
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    if is_superuser:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            query = AgentTask.objects.prefetch_related("sources", "agent_scenario", "tenant")
            if filter_tenant_id is not None:
                query = query.filter(tenant_id=filter_tenant_id)
            tasks = await query.order_by(AgentTask.created_at.desc())
    else:
        tasks = await (
            AgentTask.objects.filter(tenant_id=tenant_id)
            .prefetch_related("sources", "agent_scenario")
            .order_by(AgentTask.created_at.desc())
        )
    if not _can_manage_job_retention(request):
        tasks = [task for task in tasks if task.job_type != "prune"]

    sources = await Source.objects.filter(tenant_id=tenant_id, owner_task_id=None).order_by(Source.name)

    scenarios = await AgentScenario.objects.filter(tenant_id=tenant_id, is_active=True).order_by(AgentScenario.name)
    permitted_source_ids = {source.id for source in sources}
    prefill_source_ids = []
    for raw_source_id in request.query_params.getlist("source_id") + request.query_params.getlist("source_ids"):
        for candidate in raw_source_id.split(","):
            if candidate.isdigit() and int(candidate) in permitted_source_ids:
                prefill_source_ids.append(int(candidate))
    prefill_source_ids = list(dict.fromkeys(prefill_source_ids))
    raw_scenario_id = request.query_params.get("scenario_id", "")
    prefill_scenario_id = (
        int(raw_scenario_id)
        if raw_scenario_id.isdigit() and any(scenario.id == int(raw_scenario_id) for scenario in scenarios)
        else None
    )
    open_add_modal = request.query_params.get("create") == "1" and perms_can(request, "agenttask", "create")
    # Which payload target keys each scenario wants (param_registry.PAYLOAD_PARAMS):
    # drives the conditional target inputs in the add modal, as on the edit page.
    scenario_analysis = {str(s.id): list(s.analysis_types or []) for s in scenarios}

    # Effective activity: a task bound to a deactivated scenario or with no
    # active source to operate on is not "active", however its flag is set.
    from app.models.managers.agent_task_manager import AgentTaskManager

    active_source_ids = {s.id for s in sources if s.is_active}
    effective = await AgentTaskManager().effective_active_map(tasks, active_source_ids)
    effective_active = {tid for tid, ok in effective.items() if ok}

    # Collect running/pending jobs per task so the UI can disable the "run now"
    # button and point the user to the existing modal instead of creating a
    # duplicate job. The status rides along: a pending job has not been claimed
    # by any worker yet, and the row says "queued", not "running".
    from app.models.scheduling.job import Job

    task_ids = [t.id for t in tasks]
    running_jobs: dict[int, dict[str, Any]] = {}  # task_id -> {id, status}
    if task_ids:
        for job in await Job.objects.filter(
            agent_task_id__in=task_ids,
            status__in=["pending", "running"],
        ):
            running_jobs[job.agent_task_id] = {"id": job.id, "status": job.status}

    raw_job_id = request.query_params.get("job_id")
    job_id = int(raw_job_id) if raw_job_id and raw_job_id.isdigit() else None

    raw_task_id = request.query_params.get("task_id")
    open_task_id = int(raw_task_id) if raw_task_id and raw_task_id.isdigit() else None
    if open_task_id is not None:
        # The editor moved from a modal to its own page; the shared `?task_id=`
        # link must land there, not open a modal the page no longer carries.
        can_edit = perms_can(request, "agenttask", "update")
        return RedirectResponse(
            f"/app/tasks/{open_task_id}/edit" if can_edit else "/app/tasks",
            status_code=303,
        )

    return render(
        request,
        "web/tasks.html",
        section="tasks",
        tasks=tasks,
        sources=sources,
        source_groups=_group_sources(sources),
        collection_defaults=_collection_edit({}),
        target_fields=TARGET_FIELD_DEFS,
        scenarios=scenarios,
        scenario_analysis=scenario_analysis,
        prefill_source_ids=prefill_source_ids,
        prefill_scenario_id=prefill_scenario_id,
        prefill_job_type=(
            "prune"
            if request.query_params.get("job_type") == "prune" and _can_manage_job_retention(request)
            else "analyze"
        ),
        prefill_retention=request.query_params.get("job_type") == "prune" and _can_manage_job_retention(request),
        open_add_modal=open_add_modal,
        effective_active=effective_active,
        unconfirmed_tasks={task.id for task in tasks if outcome_unconfirmed(task.last_error)},
        running_jobs=running_jobs,
        job_types=_web_job_type_choices(allow_retention=_can_manage_job_retention(request)),
        trigger_types=BotTriggerType.choices(),
        action_types=AgentActionType.choices(),
        cron_to_human=cron_to_human,
        job_id=job_id,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
    )


@router.get("/{task_id}")
async def task_detail(request: Request, task_id: int):
    """One task: what it does, what sources it touches, and its recent runs."""
    from contextlib import nullcontext

    from app.core.tenant_context import tenant_scope
    from app.models.analysis.agent_scenario import AgentScenario
    from app.models.collection.source import Source
    from app.models.scheduling.agent_task import AgentTask
    from app.models.scheduling.job import Job

    user = getattr(request.state, "web_user", None)
    is_superuser = False
    filter_tenant_id, _tenants = await tenant_filter_context(request, is_superuser)

    source = None
    with tenant_scope(bypass=True) if is_superuser else nullcontext():
        source = (
            await AgentTask.objects.filter(id=task_id)
            .select_related("tenant", "agent_scenario")
            .prefetch_related("sources")
            .first()
        )
        if source is not None and not is_superuser and source.tenant_id != request.state.tenant_id:
            source = None
        if source is not None and source.job_type == "prune" and not _can_manage_job_retention(request):
            source = None
        if source is None:
            return render(
                request,
                "web/task_detail.html",
                section="tasks",
                task=None,
                filter_tenant_id=filter_tenant_id,
            )

        # Recent jobs for this task.
        recent_jobs = await (
            Job.objects.filter(agent_task_id=task_id, tenant_id=source.tenant_id)
            .order_by(Job.created_at.desc())
            .limit(10)
        )

        # Running/pending job for this task (UI guard against duplicate runs).
        # The status rides along: a pending job has not been claimed by any worker
        # yet, and the page says "queued", not "running".
        running_job_id: int | None = None
        running_job_status: str | None = None
        for job in recent_jobs:
            if job.status in ("pending", "running"):
                running_job_id = job.id
                running_job_status = job.status
                break
        if running_job_id is None:
            # The running job may be outside the recent-10 window.
            running_job = await Job.objects.filter(
                agent_task_id=task_id,
                tenant_id=source.tenant_id,
                status__in=["pending", "running"],
            ).first()
            if running_job is not None:
                running_job_id = running_job.id
                running_job_status = running_job.status

        # Linked sources for display; effective activity also needs all active
        # tenant sources because an empty source set means "all active sources".
        tenant_sources = await Source.objects.filter(tenant_id=source.tenant_id)
        linked_ids = set(await _task_source_ids(task_id))
        linked_sources = sorted((item for item in tenant_sources if item.id in linked_ids), key=lambda item: item.name)
        active_source_ids = {item.id for item in tenant_sources if item.is_active}

        # Scenario.
        scenario = None
        if source.agent_scenario_id is not None:
            scenario = await AgentScenario.objects.get(id=source.agent_scenario_id, tenant_id=source.tenant_id)
        from app.models.managers.agent_task_manager import AgentTaskManager

        effective = await AgentTaskManager().effective_active_map([source], active_source_ids)
        effective_active = {task_pk for task_pk, active in effective.items() if active}

    # The same outcome classification the run-now modal uses, so a run reads
    # the same on this page and in the modal: a half-broken collect is not a
    # green "готово" in one place and "частично" in the other.
    job_outcomes = {job.id: _run_outcome(job.job_type, job.result or {}) for job in recent_jobs if job.status == "done"}
    pipeline_outcomes = {}
    if perms_can(request, "source", "view") and perms_can(request, "aianalytics", "view"):
        from app.services.task_results import task_pipeline_outcome

        can_view_digest = perms_can(request, "digestrun", "view")
        with tenant_scope(source.tenant_id):
            for job in recent_jobs:
                projected = await task_pipeline_outcome(job.result, can_view_preview=can_view_digest)
                if projected:
                    pipeline_outcomes[job.id] = projected
    run_scope = await _task_run_scope(source, include_sources=perms_can(request, "source", "view"))
    latest_job = next((item for item in recent_jobs if item.id == running_job_id), None)
    if latest_job is None:
        latest_job = running_job if running_job_id is not None else (recent_jobs[0] if recent_jobs else None)
    latest_run = _task_run_panel_data(request, source, latest_job, run_scope=run_scope)
    await _attach_task_llm_diagnostics(request, source, latest_job, latest_run)
    await _attach_task_lane_evidence(latest_run, latest_job)

    # Reuse the permission-filtered canonical reader under this task's concrete
    # workspace. The editor remains the write surface; this summary only reads.
    task_definition = {"plan": None, "plan_status": "task_access_required"}
    if type(source.tenant_id) is int and perms_can(request, "agenttask", "view"):
        from app.services.task_results import task_configuration

        with tenant_scope(source.tenant_id):
            try:
                task_definition = await task_configuration(task_id) or task_definition
            except PermissionError:
                # Keep the detail page useful while hiding inaccessible plan data.
                pass

    return render(
        request,
        "web/task_detail.html",
        section="tasks",
        task=source,
        filter_tenant_id=filter_tenant_id,
        recent_jobs=recent_jobs,
        running_job_id=running_job_id,
        running_job_status=running_job_status,
        job_outcomes=job_outcomes,
        pipeline_outcomes=pipeline_outcomes,
        effective_active=effective_active,
        latest_run=latest_run,
        sources=linked_sources,
        scenario=scenario,
        task_definition=task_definition,
        collection=_collection_edit(source.payload),
        cron_to_human=cron_to_human,
        JOB_TYPE_TITLES=JOB_TYPE_TITLES,
        perms_can=perms_can,
    )


@router.get("/{task_id}/latest-progress")
async def task_latest_progress(request: Request, task_id: int):
    """Refresh only the newest Job belonging to this tenant's requested task."""
    from fastapi.responses import HTMLResponse

    if not perms_can(request, "agenttask", "view"):
        return HTMLResponse('<p class="text-sm text-slate-600">Нет доступа к задаче.</p>', status_code=403)
    tenant_id = getattr(request.state, "tenant_id", None)
    if type(tenant_id) is not int:
        return HTMLResponse('<p class="text-sm text-slate-600">Задача не найдена.</p>', status_code=404)
    task = await AgentTask.objects.filter(id=task_id, tenant_id=tenant_id).first()
    if task is None:
        return HTMLResponse('<p class="text-sm text-slate-600">Задача не найдена.</p>', status_code=404)
    job = (
        await Job.objects.filter(agent_task_id=task.id, tenant_id=tenant_id, status__in=["pending", "running"])
        .order_by(Job.created_at.desc())
        .first()
    )
    if job is None:
        job = (
            await Job.objects.filter(agent_task_id=task.id, tenant_id=tenant_id).order_by(Job.created_at.desc()).first()
        )
    run_scope = await _task_run_scope(task, include_sources=perms_can(request, "source", "view"))
    panel = _task_run_panel_data(request, task, job, run_scope=run_scope)
    await _attach_task_llm_diagnostics(request, task, job, panel)
    await _attach_task_lane_evidence(panel, job)
    return render(request, "web/task_run_progress.html", task=task, sidebar_oob=True, **panel)


@router.get("/{task_id}/edit")
async def task_edit_page(request: Request, task_id: int):
    """The task editor as a page, not a modal.

    The edit form outgrew the viewport — a modal's height is bounded by the
    screen, this form already scrolls — so the editor lives at its own URI
    (`/app/tasks/{id}/edit`) like every other editor. The row's name button
    links here, and the old `?task_id=` parameter on the list page redirects
    here. The superuser branch mirrors `task_detail`: the task is read under
    bypass and its own tenant decides the page's sources and scenarios.
    """
    from contextlib import nullcontext

    from app.core.tenant_context import tenant_scope

    user = getattr(request.state, "web_user", None)
    is_superuser = False
    filter_tenant_id, _ = await tenant_filter_context(request, is_superuser)

    denied = guard_web(request, "agenttask", "update", back="/app/tasks")
    if denied is not None:
        return denied

    with tenant_scope(bypass=True) if is_superuser else nullcontext():
        # `sources` is a lazy m2m and the row comes back detached — without a
        # prefetch touching it raises DetachedInstanceError (see `_task_source_ids`).
        task = (
            await AgentTask.objects.filter(id=task_id)
            .select_related("agent_scenario")
            .prefetch_related("sources")
            .first()
        )
        if task is not None and not is_superuser and task.tenant_id != request.state.tenant_id:
            task = None
        if task is None:
            add_flash(request, "error", "Задача не найдена")
            return RedirectResponse("/app/tasks", status_code=302)
        if task.job_type == "prune" and not _can_manage_job_retention(request):
            add_flash(
                request,
                "error",
                "Просматривать настройку хранения заданий могут только администратор и владелец пространства",
            )
            return RedirectResponse("/app/tasks", status_code=302)
        sources = await Source.objects.filter(tenant_id=task.tenant_id).order_by(Source.name)
        # A query lane owns a task-scoped Source (`owner_task_id`): the owning
        # task may show it as a linked row, but it is never a pickable option
        # and another task never sees it at all.
        sources = [source for source in sources if source.owner_task_id in (None, task.id)]
        scenarios = await AgentScenario.objects.filter(tenant_id=task.tenant_id, is_active=True).order_by(
            AgentScenario.name
        )
        # The dropdown lists active scenarios, but the task's own scenario must
        # show even when it is inactive: it was set (via admin/CLI) and the
        # editor must render what the task is bound to, not silently read as
        # empty. Without this a deactivated scenario made the field look blank.
        if task.agent_scenario_id is not None and not any(s.id == task.agent_scenario_id for s in scenarios):
            bound = await AgentScenario.objects.get(id=task.agent_scenario_id, tenant_id=task.tenant_id)
            if bound is not None:
                scenarios = [bound, *scenarios]

    # Resolve the m2m into a plain list: the row is detached here, and
    # `_edit_payload` must not hit a lazy loader on it (DetachedInstanceError).
    source_ids_for_task: list[int] = []
    if task.sources is not None:
        try:
            source_ids_for_task = [s.id for s in task.sources]
        except Exception:
            # Fallback: read via the manager when the relationship is already
            # garbage-collected or otherwise inaccessible.
            source_ids_for_task = await AgentTaskManager().get_sources(task_id)

    active_source_ids = {s.id for s in sources if s.is_active}
    effective = await AgentTaskManager().effective_active_map([task], active_source_ids)
    effective_active = {tid for tid, ok in effective.items() if ok}

    edit = _edit_payload(task, effective_active, source_ids_for_task)
    if not (task.payload or {}).get("task_plan") and source_ids_for_task:
        edit["platforms"] = list(
            dict.fromkeys(
                (
                    getattr(getattr(source, "platform", None), "platform_type", None).db_value
                    if hasattr(getattr(getattr(source, "platform", None), "platform_type", None), "db_value")
                    else str(getattr(getattr(source, "platform", None), "platform_type", "vk"))
                )
                for source in (task.sources or [])
                if source.id in source_ids_for_task
            )
        ) or ["vk"]

    # Which payload targets each scenario's analysis types want — the form
    # renders the matching inputs (see `param_registry.PAYLOAD_PARAMS`).
    scenario_analysis = {str(s.id): list(s.analysis_types or []) for s in scenarios}

    return render(
        request,
        "web/task_edit.html",
        section="tasks",
        task=task,
        edit=edit,
        sources=sources,
        source_groups=_group_sources([source for source in sources if source.owner_task_id is None]),
        task_sources=[source for source in sources if source.owner_task_id is not None],
        target_fields=TARGET_FIELD_DEFS,
        scenarios=scenarios,
        scenario_analysis=scenario_analysis,
        job_types=_web_job_type_choices(allow_retention=_can_manage_job_retention(request)),
        trigger_types=BotTriggerType.choices(),
        action_types=AgentActionType.choices(),
        can_dry_run=_can_manage_job_retention(request),
        cron_to_human=cron_to_human,
        is_superuser=is_superuser,
        filter_tenant_id=filter_tenant_id,
    )


@router.post("/{task_id}/toggle")
async def task_toggle(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "agenttask", "update", back="/app/tasks")
    if denied is not None:
        return denied

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)
    if task.job_type == "prune" and not _can_manage_job_retention(request):
        add_flash(
            request, "error", "Управлять настройкой хранения заданий могут только администратор и владелец пространства"
        )
        return RedirectResponse("/app/tasks", status_code=302)

    new_active = not task.is_active
    if new_active:
        from app.models.managers.agent_task_manager import AgentTaskManager

        linked = await AgentTaskManager().get_sources(task.id)
        blocked = await _can_activate(tenant_id, task.job_type, linked, task.agent_scenario_id, payload=task.payload)
        if blocked is not None:
            add_flash(request, "error", f"Задача «{task.name}» не активирована: {blocked}")
            return RedirectResponse("/app/tasks", status_code=302)
        if task.job_type == "analyze" and isinstance(task.payload, dict) and task.payload.get("collect_before_analyze"):
            scenario_block = await _unified_analysis_scenario_block(tenant_id, task.agent_scenario_id)
            if scenario_block is not None:
                add_flash(request, "error", f"Для запуска анализа соцсетей {scenario_block}")
                return RedirectResponse("/app/tasks", status_code=302)

    await AgentTask.objects.update_by_id(task.id, is_active=new_active)
    action = "активирована" if new_active else "деактивирована"
    add_flash(request, "success", f"Задача «{task.name}» {action}")
    return RedirectResponse("/app/tasks", status_code=302)


@router.post("/{task_id}/run-now")
async def task_run_now(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
    return_to_detail: str = Form(""),
):
    back = f"/app/tasks/{task_id}" if return_to_detail == "1" else "/app/tasks"
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    # Enqueues a job — the same right the sqladmin run-now action declares.
    denied = guard_web(request, "agenttask", "update", back=back)
    if denied is not None:
        return denied

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)
    if task.job_type == "prune" and not _can_manage_job_retention(request):
        add_flash(
            request, "error", "Запускать настройку хранения заданий могут только администратор и владелец пространства"
        )
        return RedirectResponse("/app/tasks", status_code=302)
    if task.job_type == "analyze" and isinstance(task.payload, dict) and task.payload.get("collect_before_analyze"):
        scenario_block = await _unified_analysis_scenario_block(tenant_id, task.agent_scenario_id)
        if scenario_block is not None:
            add_flash(request, "error", f"Для запуска анализа соцсетей {scenario_block}")
            return RedirectResponse("/app/tasks", status_code=302)

    # A manual run replaces the task's unfinished work: the admission boundary
    # deletes that task's pending/running jobs before inserting the new one. A
    # job pending for *another* task on the same sources is still reported as
    # busy — replacing it would cancel somebody else's run.
    outcome = await queue_task_now(task, getattr(request.state.web_user, "id", None))
    if outcome.get("status") == "blocked":
        add_flash(request, "error", f"Задача «{task.name}» не запущена: {outcome.get('reason')}")
        return RedirectResponse(back, status_code=302)
    if outcome.get("already_running"):
        add_flash(request, "info", "Источник уже занят сбором или анализом. Новый запуск не создан")
    else:
        add_flash(request, "success", f"Задача «{task.name}» поставлена в очередь на выполнение")
    job_id = (outcome or {}).get("job_id")
    if not job_id:
        return RedirectResponse(back, status_code=302)
    return RedirectResponse(back if return_to_detail == "1" else f"/app/tasks?job_id={job_id}", status_code=302)


@router.post("/{task_id}/retry-analysis")
async def task_retry_analysis(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
):
    """Reset exhausted rows and queue a retry within this analysis task's scope."""
    back = f"/app/tasks/{task_id}"
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)
    denied = guard_web(request, "agenttask", "update", back=back)
    if denied is not None:
        return denied
    tenant_id = getattr(request.state, "tenant_id", None)
    if type(tenant_id) is not int:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)
    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None or task.job_type != "analyze":
        add_flash(request, "error", "Повторный анализ доступен только для задачи анализа соцсетей")
        return RedirectResponse(back, status_code=302)

    from app.jobs.dispatcher import launch_job_background
    from app.jobs.enqueue import enqueue_task_run
    from app.jobs.source_activity import SourceJobBusy

    try:
        job = await enqueue_task_run(
            task,
            initiated_by_user_id=getattr(request.state.web_user, "id", None),
            retry_exhausted=True,
        )
    except ValueError:
        add_flash(request, "error", "Проверьте период задачи: даты должны быть корректными и идти по порядку")
        return RedirectResponse(back, status_code=302)
    except SourceJobBusy:
        add_flash(request, "info", f"Задача «{task.name}» уже выполняется или ждёт в очереди")
        return RedirectResponse(back, status_code=302)
    if job is None:
        add_flash(request, "info", "В источниках этой задачи нет сырых материалов в настроенном периоде")
        return RedirectResponse(back, status_code=302)
    launch_job_background(job.id, allow_retry=False)
    add_flash(request, "success", f"Повторный анализ по задаче «{task.name}» поставлен в очередь")
    return RedirectResponse(back, status_code=302)


@router.post("/{task_id}/digest/{job_id}/send")
async def task_send_generated_digest(
    request: Request,
    task_id: int,
    job_id: int,
    confirmed: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    """Send one already generated task digest after a separate confirmation."""
    back = f"/app/tasks/{task_id}"
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)
    denied = guard_web(request, "digestrun", "create", back=back)
    if denied is not None:
        return denied
    if confirmed != "true":
        add_flash(request, "info", "Подтвердите отдельную отправку дайджеста")
        return RedirectResponse(back, status_code=302)
    tenant_id = getattr(request.state, "tenant_id", None)
    if type(tenant_id) is not int:
        return Response(status_code=403)
    from app.core.tenant_context import tenant_scope
    from app.services.task_digest_delivery import send_task_digest

    try:
        with tenant_scope(tenant_id):
            outcome = await send_task_digest(task_id=task_id, job_id=job_id)
    except Exception:
        add_flash(request, "error", "Не удалось отправить сохранённый дайджест; проверьте результат задачи")
    else:
        if isinstance(outcome, dict) and outcome.get("status") == "sent":
            add_flash(request, "success", "Дайджест отправлен")
        elif isinstance(outcome, dict) and outcome.get("status") == "skipped":
            add_flash(request, "info", "Эта отправка уже обработана")
        elif isinstance(outcome, dict) and outcome.get("status") == "uncertain":
            add_flash(request, "error", "Подтверждение доставки не получено. Проверьте журнал отправки перед повтором")
        elif isinstance(outcome, dict) and outcome.get("status") == "partial":
            add_flash(request, "error", "Дайджест доставлен частично. Проверьте журнал перед повторной отправкой")
        elif isinstance(outcome, dict) and outcome.get("status") == "blocked":
            add_flash(request, "error", "Отправка заблокирована текущими правами или настройками адресатов")
        else:
            add_flash(request, "error", "Дайджест не отправлен; проверьте его состояние")
    return RedirectResponse(back, status_code=302)


@router.post("/{task_id}")
async def task_update(
    request: Request,
    task_id: int,
    name: str = Form(...),
    job_type: str = Form(...),
    cron_expr: str = Form(...),
    is_active: str = Form(""),
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
    return_to_detail: str = Form(""),
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
):
    back = f"/app/tasks/{task_id}/edit" if return_to_detail == "1" else "/app/tasks"
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

    denied = guard_web(request, "agenttask", "update", back=back)
    if denied is not None:
        return denied

    dry_run_flag = dry_run == "on"
    if dry_run_flag and not _can_manage_job_retention(request):
        add_flash(request, "warning", "Тестовый прогон доступен только администратору и владельцу пространства")
        dry_run_flag = False

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse(back, status_code=302)
    if task.job_type == "prune" and not _can_manage_job_retention(request):
        add_flash(
            request, "error", "Управлять настройкой хранения заданий могут только администратор и владелец пространства"
        )
        return RedirectResponse("/app/tasks", status_code=302)

    cron_expr = cron_expr.strip()

    # Saving under a name another task already holds would fail the unique
    # constraint; `exclude_id` keeps a task from clashing with its own row.
    clash = await _duplicate_name(request, back, name.strip()[:100], exclude_id=task_id)
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
        new_payload = _payload_from_form(
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
            apply_selection(new_payload, tracked_users, _split_phrases(keywords_list), keywords_match)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

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

    # Merge into existing payload instead of replacing — keys like "period",
    # "days", "min_messages" set via CLI or agent tool must survive a web edit.
    payload = new_payload if job_type == "prune" else (task.payload.copy() if isinstance(task.payload, dict) else {})
    if job_type != "prune":
        payload.update(new_payload)
    if job_type in ("collect", "analyze") and window_type == "rolling":
        payload.pop("cli_dates", None)
    requested_active = is_active == "on"
    if job_type == "analyze":
        scenario_block = await _unified_analysis_scenario_block(
            tenant_id, scenario_id, allow_inactive_selected=not requested_active and not run_now
        )
        if scenario_block is not None:
            add_flash(request, "error", f"Для анализа соцсетей {scenario_block}")
            return RedirectResponse(back, status_code=302)
    if job_type == "analyze":
        payload.pop("analyze_inline", None)
    # Editing through the web form removes legacy force keys. Manual web runs
    # also override stored legacy values in the one-off job payload above.
    payload.pop("force_refresh", None)
    payload.pop("force_reanalyze", None)
    # The form's target fields are authoritative for their own keys: filled
    # sets, emptied clears (see `_apply_targets`).
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

    # Same contract as create: the form that carries the block is authoritative,
    # a legacy submission leaves the stored block untouched.
    if collection_configured == "1":
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
    if job_type in ("collect", "analyze") and source_scope != "legacy":
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
                existing_vk_access=((task.payload or {}).get("task_plan") or {}).get("vk_access"),
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
                from app.services.task_plans import apply_task_plan

                payload = apply_task_plan(payload, task_plan, job_type=job_type)
                parsed_source_ids = list(task_plan["source_ids"])
        except ValueError as error:
            add_flash(request, "error", f"Проверьте план задачи: {error}")
            return RedirectResponse(back, status_code=302)

    if job_type == "digest":
        payload.pop("task_plan", None)

    # Warn about missing target params for the scenario's analysis types —
    # the scenario this save binds, not the one the row held before the edit.
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

    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenants
    from app.tasks.cron import next_run_at, resolve_tz

    # Same rule as create: the workspace zone, so an edit does not silently
    # re-schedule the task in the global zone the runner would keep using.
    tenant = await tenants.get(id=tenant_id)
    from app.services.task_plans import task_plan_timezone

    stored_plan = (task.payload or {}).get("task_plan") if isinstance(task.payload, dict) else None
    tz = task_plan_timezone(task_plan or stored_plan, resolve_tz(tenant))

    if cron_expr == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(cron_expr, tz)

    try:
        await _check_task_sources(parsed_source_ids, tenant_id, task_id=task_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    # Same rule as create: a ticked-but-off source activates with the save, so
    # the activation check below sees the sources as the operator intends them.
    if AgentTaskManager.requires_sources(job_type):
        await _activate_selected_sources(tenant_id, parsed_source_ids)

    # Activation is allowed only when the task has an active scenario and, for a
    # source-based type, at least one active source; otherwise it stays inactive.
    activation_blocked = None
    if requested_active:
        activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id, payload=payload)
        if activation_blocked is not None:
            add_flash(
                request,
                "warning",
                f"Задача «{name}» не активирована: {activation_blocked}",
            )

    # The row and its links are one unit: a source that vanished between the
    # check above and this write must abort the whole save, not leave the task
    # active and link-less (which the runtime reads as "all active sources").
    from app.core.database import new_session

    session = new_session()
    try:
        with tenant_scope(tenant_id):
            async with session.begin():
                await AgentTask.objects.update_by_id(
                    task.id,
                    name=name.strip()[:100],
                    job_type=job_type,
                    cron_expr=cron_expr,
                    payload=payload,
                    agent_scenario_id=None if job_type == "prune" else scenario_id,
                    is_active=requested_active and activation_blocked is None,
                    next_run_at=next_run,
                    dry_run=dry_run_flag,
                    **trigger_action_fields,
                    session=session,
                )
                await _replace_task_sources(task.id, parsed_source_ids, tenant_id, session=session)
    except ValueError:
        add_flash(
            request,
            "error",
            "Источники задачи изменились во время сохранения — обновите страницу и повторите",
        )
        return RedirectResponse(back, status_code=302)
    finally:
        await session.close()

    # Queued, not run in-request, for the same reason as the create branch: a long
    # job froze the page and invited the second click that hit the unique name.
    if run_now:
        updated = await AgentTask.objects.get(id=task.id, tenant_id=tenant_id)
        outcome = await queue_task_now(updated, getattr(request.state.web_user, "id", None))
        if outcome.get("already_running"):
            add_flash(request, "info", f"Задача «{name}» обновлена, но источник уже занят. Новый запуск не создан")
        else:
            add_flash(request, "success", f"Задача «{name}» обновлена и поставлена в очередь на выполнение")
        job_id = (outcome or {}).get("job_id")
        if return_to_detail == "1":
            return RedirectResponse(f"/app/tasks/{task_id}", status_code=302)
        if not job_id:
            return RedirectResponse("/app/tasks", status_code=302)
        return RedirectResponse(f"/app/tasks?job_id={job_id}", status_code=302)
    else:
        add_flash(request, "success", f"Задача «{name}» обновлена")
    return RedirectResponse(f"/app/tasks/{task_id}" if return_to_detail == "1" else "/app/tasks", status_code=302)


@router.post("/{task_id}/delete")
async def task_delete(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "agenttask", "delete", back="/app/tasks")
    if denied is not None:
        return denied

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    task_name = task.name
    await AgentTask.objects.delete(id=task_id)
    add_flash(request, "success", f"Задача «{task_name}» удалена")
    return RedirectResponse("/app/tasks", status_code=302)


__all__ = [name for name in globals() if not name.startswith("__")]
