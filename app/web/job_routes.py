"""HTTP routes for job status and cancellation."""

from __future__ import annotations

from .task_shared import (
    AgentTask,
    Any,
    Form,
    Job,
    Request,
    ensure_csrf,
    perms_can,
    project_run,
    router,
)

from .task_presenters import (
    _as_int,
    _diagnostics_admin,
    _diagnostics_view,
    _job_summary,
    _source_links,
)


@router.get("/job/{job_id}/status")
async def job_status(request: Request, job_id: int):
    """Poll job status (run-now modal). Returns JSON; tenant-scoped."""
    from fastapi.responses import JSONResponse

    tenant_id = request.state.tenant_id
    if not (perms_can(request, "agenttask", "view") or perms_can(request, "source", "view")):
        return JSONResponse({"status": "not_found", "label": "Задача не найдена"}, status_code=404)
    job = await Job.objects.filter(id=job_id, tenant_id=tenant_id).first()
    if job is None:
        return JSONResponse({"status": "not_found", "label": "Задача не найдена"})
    task_name = None
    task = None
    if job.agent_task_id is not None:
        task = await AgentTask.objects.get(id=job.agent_task_id, tenant_id=tenant_id)
        task_name = task.name if task else None
        if task is not None and job.job_type == "collect":
            payload = task.payload if isinstance(task.payload, dict) else {}
            job._analysis_inline = bool(payload.get("analyze_inline", False))
    summary = _job_summary(job, task_name)
    summary.pop("hint", None)
    if summary.get("status") == "failed":
        summary["error"] = "Задача завершилась с ошибкой. Повторите запуск позже."
    # Normal JSON never carries diagnostics: the allowlisted detail is added
    # only for a workspace admin role or a platform admin.
    if _diagnostics_admin(request):
        summary["diagnostics"] = _diagnostics_view(job)
    else:
        summary.pop("stats", None)
    # Mini-links to the sources this run touched; they need the DB, so they are
    # resolved here rather than in the pure `_job_summary`.
    can_view_sources = perms_can(request, "source", "view")
    summary["sources"] = _source_links(job) if can_view_sources else []
    if not can_view_sources:
        summary.pop("raw_coverage", None)
        summary.pop("progress", None)
        if isinstance(summary.get("diagnostics"), dict):
            summary["diagnostics"].pop("selected", None)
            summary["diagnostics"].pop("remaining", None)
    if summary.get("status") in ("pending", "running"):
        summary["can_cancel"] = bool(perms_can(request, "agenttask", "update"))
    from .deps import templates

    view = project_run(job, task, authorized=can_view_sources)
    if view:
        view["state"] = "unconfirmed" if summary.get("heartbeat_unconfirmed") else summary["status"]
        summary["run_progress_html"] = str(
            templates.get_template("web/_run_progress.html").module.record_progress(view, compact=True)
        )
        summary["task_url"] = f"/app/tasks/{task.id}" if task is not None else None
    return JSONResponse(summary)

@router.post("/job/{job_id}/cancel")
async def job_cancel_from_modal(
    request: Request,
    job_id: int,
    token: str = Form("", alias="_csrf"),
    content: str = Form(""),
):
    """Cancel the run shown in the run-status modal (JSON; tenant-scoped).

    `pending` (never claimed) is stopped before start; `running` is cancelled
    only for the generation observed here, like the queue page. For a collect
    run `content` chooses whether the rows it already staged stay (`keep`) or
    go (`discard`). Cancellation fences the Job write; it does not recall an
    external effect that already happened.
    """
    from fastapi.responses import JSONResponse

    def reply(ok: bool, message: str, status_code: int = 200, **extra: Any) -> JSONResponse:
        return JSONResponse({"ok": ok, "message": message, **extra}, status_code=status_code)

    if not ensure_csrf(request, token):
        return reply(False, "Сессия истекла — обновите страницу и попробуйте ещё раз.", 403)
    if not perms_can(request, "agenttask", "update"):
        return reply(False, "Недостаточно прав, чтобы отменить задачу.", 403)
    if content not in ("", "keep", "discard"):
        return reply(False, "Неизвестный вариант для собранных записей.", 400)
    tenant_id = getattr(request.state, "tenant_id", None)
    if type(tenant_id) is not int:
        return reply(False, "Задача не найдена.", 404)

    from app.core.tenant_context import tenant_scope
    from app.jobs.claim_outcomes import JobClaim

    with tenant_scope(tenant_id):
        # Two passes: a pending row may be claimed between the read and the write.
        for _ in range(2):
            job = await Job.objects.filter(id=job_id, tenant_id=tenant_id).first()
            if job is None:
                return reply(False, "Задача не найдена.", 404)
            if job.status == "pending":
                if _as_int(getattr(job, "attempts", 0)):
                    return reply(False, "Задача ждёт повторной попытки — отменить её отсюда нельзя.", 409)
                if await Job.objects.cancel_pending(job.id):
                    return reply(True, "Запуск отменён до начала.")
                continue
            if job.status == "running":
                try:
                    observed = JobClaim.capture(job)
                except ValueError:
                    return reply(False, "Отмена не подтверждена: данные запуска неполны.", 409)
                discard = (content == "discard") if job.job_type == "collect" else None
                if await Job.objects.cancel_running(observed, discard_collected=discard):
                    return reply(True, "Задача остановлена.")
                continue
            return reply(False, "Задача уже завершилась — отменять нечего.", 409, status=job.status)
    return reply(False, "Состояние задачи изменилось — проверьте результат.", 409)

__all__ = [name for name in globals() if not name.startswith("__")]
