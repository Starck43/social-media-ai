"""Job queue `/app/jobs` — what ran, what failed, and a manual re-run.

The queue is the one piece of the runtime a user cannot otherwise see: a task
that silently stopped firing looks exactly like a task that never existed.
This page is the answer to "почему моя задача не выполняется" — pending/running
rows show the scheduler is alive, `failed` rows carry the error text, and a
failed job can be re-run from here.

Re-running is not "run now" for a task (that is on `/app/tasks`): it re-executes
*this exact job row* through the dispatcher's claim, so the retry counter and
the attempt log stay honest. A `running` job cannot be re-run — it is already
claimed, and a second claim would run the same work twice.

The same `running` row is also the one row that cannot be *deleted*: the worker
holds it and will call `mark_done`/`mark_failed` on it, so removing the row
underneath a live claim loses the audit trail of work in flight and leaves the
dispatcher updating a row that no longer exists. Everything else is history (or
scheduled work the operator is cancelling on purpose) and may go — which is
what the per-row trash button and «Очистить всё» do.

The page is restricted to the active workspace and its explicit job permissions.
Platform operator status never widens its rows or actions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX, outcome_unconfirmed
from app.models.scheduling.job import Job

from .deps import (
    action_tenant_id,
    add_flash,
    ensure_csrf,
    guard_web,
    plural,
    render,
    tenant_filter_context,
)

router = APIRouter(prefix="/jobs")

# Enough rows to spot a stuck or failing schedule without turning the page into
# a log viewer; the queue is a status screen, and `/app/tasks` is the place to
# manage the recurring work itself.
HISTORY_LIMIT = 50
RETRYABLE = frozenset({"failed"})  # compatibility for callers; replay uses can_replay() per row.

from app.jobs.replay_policy import can_replay, replay_block_reason

# Statuses a delete may touch. `running` is claimed by the worker *right now* —
# see the module docstring for why that row is off limits.
DELETABLE = frozenset({"pending", "done", "failed"})

# Statuses a cancel may touch. Only `running` — anything else is already
# terminal or not yet in flight.
CANCELLABLE = frozenset({"running"})
_REVIEW_CODES = ("partial_delivery_requires_review", "digest_run_in_progress_requires_review")


def _safe_job_diagnostic(job: Job) -> dict[str, str]:
    """Return fixed, non-sensitive guidance; never render raw exception text."""
    result = job.result if isinstance(job.result, dict) else {}
    raw = " ".join(str(value) for value in (job.error or "", result.get("diagnostic_code", ""))).lower()
    error_kind = ""
    if type(job.error) is str and job.error.startswith("Queue outcome unconfirmed; inspect before retry. "):
        candidate = job.error.removeprefix("Queue outcome unconfirmed; inspect before retry. ").split(" ", 1)[0]
        if candidate in {"timeout", "connection_error", "io_error", "value_error", "type_error", "runtime_error", "unknown_error"}:
            error_kind = candidate
    if error_kind == "timeout":
        return {"code": "timeout", "stage": "handler", "job_id": str(job.id), "counts": "", "title": "Время ожидания истекло", "detail": "Внешняя операция не ответила вовремя; результат мог быть неполным.", "action": "Проверьте состояние платформы перед повторным запуском."}
    if error_kind == "connection_error":
        return {"code": error_kind, "stage": "handler", "job_id": str(job.id), "counts": "", "title": "Ошибка соединения", "detail": "Подключение к внешнему сервису не установлено.", "action": "Проверьте доступность сервиса и состояние результата перед повтором."}
    if error_kind == "io_error":
        return {"code": error_kind, "stage": "handler", "job_id": str(job.id), "counts": "", "title": "Ошибка ввода-вывода", "detail": "Операция с сетью или хранилищем завершилась ошибкой.", "action": "Проверьте инфраструктуру и сохранённый результат."}
    diagnostic = {"code": "job_failed", "stage": "handler", "title": "Ошибка выполнения", "detail": "Обработчик не завершил задание.", "action": "Проверьте параметры задачи и журнал сервера; затем повторите запуск."}
    if outcome_unconfirmed(job.error):
        diagnostic.update(code="outcome_unconfirmed", title="Результат не подтверждён", detail="Возможный внешний эффект нужно проверить вручную.", action="Проверьте целевую платформу перед повторным запуском.")
    elif "partial_delivery_requires_review" in raw:
        diagnostic.update(code="partial_delivery_requires_review", stage="delivery", title="Частичная отправка", detail="Часть внешних действий могла выполниться.", action="Сверьте отправленные материалы и продолжите вручную.")
    elif "digest_run_in_progress_requires_review" in raw:
        diagnostic.update(code="digest_run_in_progress_requires_review", stage="delivery", title="Отправка дайджеста требует проверки", detail="Нельзя подтвердить итог доставки.", action="Проверьте историю отправок до повтора.")
    elif job.status == "done" and _job_has_attention(job):
        diagnostic.update(code="partial_result", stage="processing", title="Частичный результат", detail="Выполнение завершилось с неполным результатом.", action="Откройте задачу и проверьте отчёт перед следующим запуском.")
    counts = result.get("counts") if isinstance(result.get("counts"), dict) else result
    measured = [f"{key}: {value}" for key, value in counts.items() if key in {"processed", "created", "updated", "failed", "skipped", "sent", "total"} and type(value) is int and value >= 0]
    diagnostic["counts"] = ", ".join(measured[:5])
    diagnostic["job_id"] = str(job.id)
    return diagnostic


def _job_requires_review(job: Job) -> bool:
    if job.status == "failed" and outcome_unconfirmed(job.error):
        return True
    result = job.result if isinstance(job.result, dict) else {}
    if any(result.get(key) is True for key in ("needs_review", "review_required", "operator_review", "delivery_review")):
        return True
    return any(
        code in str(result.get(key, "")).lower() or code in str(job.error or "").lower()
        for code in _REVIEW_CODES
        for key in ("status", "error", "diagnostic_code", "delivery_status")
    )


def _job_has_attention(job: Job) -> bool:
    """Conservative projection for done rows surfaced in the active view."""
    if job.status != "done":
        return False
    if _job_requires_review(job):
        return True
    result = job.result if isinstance(job.result, dict) else {}
    def text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return "[malformed]"
        if isinstance(value, bool):
            return str(value).lower()
        return str(value).strip().lower()

    if text(result.get("status")) in {"partial", "partial_success", "failed", "error", "requires_review"}:
        return True
    if any(text(result.get(key)) in {"true", "1", "yes"} for key in ("needs_review", "review_required", "operator_review", "delivery_review")):
        return True
    for key in ("status", "error", "diagnostic_code", "delivery_status"):
        value = text(result.get(key))
        if any(code in value for code in _REVIEW_CODES):
            return True
    for key in ("error", "errors", "staged_errors", "rate_limited", "collection_errors", "collection_rate_limited"):
        value = text(result.get(key))
        if value not in {"", "0", "false", "null", "none"}:
            # Malformed non-scalar counters are visible for review, never cast.
            return True
    return False


def _safe_terminal_delete_criteria():
    from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
    from app.jobs.recovery_policy import OPERATOR_CANCELLED_ERROR, OUTCOME_UNCONFIRMED_PREFIX

    safe_error = or_(
        Job.error.is_(None),
        and_(
            Job.error != OPERATOR_CANCELLED_ERROR,
            Job.error != "Cancelled before start",
            ~Job.error.startswith(OUTCOME_UNCONFIRMED_PREFIX),
            ~Job.error.startswith(ATTEMPT_BUDGET_STOP_PREFIX),
            *(~func.lower(Job.error).contains(code) for code in _REVIEW_CODES),
        ),
    )
    safe_result = and_(
        *(
            or_(
                Job.result[key].as_string().is_(None),
                func.lower(Job.result[key].as_string()).notin_(("true", "1", *_REVIEW_CODES)),
            )
            for key in (
                "needs_review",
                "review_required",
                "operator_review",
                "delivery_review",
                "status",
                "error",
                "diagnostic_code",
                "delivery_status",
            )
        )
    )
    return safe_error, safe_result

# Queue-health thresholds, shown as counts and ages only (no payload/error text).
# A due job still `pending` this long after `run_at` was not claimed: the worker
# loop is stopped or busy with one long job.
OVERDUE_PENDING_MINUTES = 15
# Mirrors the default cutoff of `JobManager.reap_stale`, which the worker loop
# runs before every claim and which quarantines older `running` rows. A row
# still `running` past it therefore means that loop (and its reaper) is not running.
STALE_RUNNING_MINUTES = 30


def _as_utc(value: datetime | None) -> datetime | None:
    """Treat a naive timestamp as UTC so comparisons never raise."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def queue_signals(rows: list[Job], now: datetime) -> dict[str, int | None]:
    """Aggregated, redacted queue-health signals over the given rows.

    `overdue_pending` / `oldest_overdue_minutes`: due rows nobody claimed.
    `stale_running`: claims older than the reaper cutoff that are still open.
    `unconfirmed_failed`: failures whose external outcome is unknown, counted
    across all rows rather than only the visible history page.
    Only counts and whole minutes are returned; no row content is exposed.
    """
    overdue_cutoff = now - timedelta(minutes=OVERDUE_PENDING_MINUTES)
    stale_cutoff = now - timedelta(minutes=STALE_RUNNING_MINUTES)
    overdue_ages: list[timedelta] = []
    stale_running = 0
    unconfirmed_failed = 0
    quarantined = 0
    for job in rows:
        if job.status == "pending":
            run_at = _as_utc(job.run_at)
            if run_at is not None and run_at <= overdue_cutoff:
                overdue_ages.append(now - run_at)
        elif job.status == "running":
            locked_at = _as_utc(job.locked_at)
            if locked_at is not None and locked_at < stale_cutoff:
                stale_running += 1
        elif job.status == "failed":
            if outcome_unconfirmed(job.error):
                unconfirmed_failed += 1
            if isinstance(job.error, str) and job.error.startswith(OUTCOME_UNCONFIRMED_PREFIX):
                quarantined += 1
    return {
        "overdue_pending": len(overdue_ages),
        "oldest_overdue_minutes": int(max(overdue_ages).total_seconds() // 60) if overdue_ages else None,
        "stale_running": stale_running,
        "unconfirmed_failed": unconfirmed_failed,
        "quarantined": quarantined,
    }


def _replayable_job(job: Job) -> bool:
    return can_replay(job)


async def _jobs_for(tenant_id: int | None) -> list[Job]:
    """Most recent jobs, newest first, for one workspace (None = all)."""
    query = Job.objects.order_by(Job.created_at.desc()).limit(HISTORY_LIMIT)
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    return list(await query)


async def _job_sources(jobs: list[Job]) -> dict[int, list[dict[str, Any]]]:
    """job_id → linked sources, so a row reads as "what this run touched".

    Sources come from the run's own `result["per_source"]` (name + id, the most
    accurate record of what actually ran), falling back to `payload["source_ids"]`
    for a job that has not finished yet. Resolved in one lookup over every id.
    """
    from app.models import Source

    by_job: dict[int, list[dict[str, Any]]] = {}
    wanted: set[int] = set()
    for job in jobs:
        per = (job.result or {}).get("per_source") or []
        entries = [{"source_id": p.get("source_id"), "name": p.get("name")} for p in per if p.get("source_id")]
        by_job[job.id] = entries
        wanted.update(e["source_id"] for e in entries)
        wanted.update(int(sid) for sid in ((job.payload or {}).get("source_ids") or []) if str(sid).isdigit())

    names: dict[int, str] = {}
    if wanted:
        rows = await Source.objects.filter(Source.id.in_(wanted))
        names = {s.id: s.name for s in rows}

    for job in jobs:
        entries = by_job[job.id]
        seen = {e["source_id"] for e in entries}
        for sid in (job.payload or {}).get("source_ids") or []:
            if not str(sid).isdigit():
                continue
            sid = int(sid)
            if sid not in seen:
                entries.append({"source_id": sid, "name": names.get(sid, f"Источник #{sid}")})
                seen.add(sid)

    return by_job


async def _queue_overview(tenant_id: int | None) -> tuple[dict[str, int], dict[str, int | None]]:
    """Queue health across the *entire* history, not just the last HISTORY_LIMIT rows.

    Status totals plus `queue_signals` over the same rows and scope.
    """
    query = Job.objects
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    rows = list(await query)
    stats = {
        "pending": sum(1 for j in rows if j.status == "pending"),
        "running": sum(1 for j in rows if j.status == "running"),
        "done": sum(1 for j in rows if j.status == "done"),
        "failed": sum(1 for j in rows if j.status == "failed"),
    }
    return stats, queue_signals(rows, datetime.now(timezone.utc))


@router.get("")
@router.get("/")
async def jobs_list(request: Request):
    """Job history for the active workspace, with the queue's health on top."""
    gated = guard_web(request, "job", "view", back="/app/")
    if gated is not None:
        return gated

    user = getattr(request.state, "web_user", None)
    is_superuser = False
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)
    tenant_id = getattr(request.state, "tenant_id", None)
    from app.core.tenant_context import tenant_scope
    web_perms = getattr(request.state, "web_perms", None)
    can_manage_retention = bool(
        web_perms and (web_perms.is_owner or web_perms.workspace_role in {"owner", "admin"})
    )

    view = request.query_params.get("view", "active")
    if view not in {"active", "history", "all"}:
        view = "active"
    raw_page = request.query_params.get("page", "1")
    if not raw_page.isdigit() or int(raw_page) < 1:
        add_flash(request, "error", "Номер страницы должен быть положительным числом")
        return RedirectResponse(f"/app/jobs?view={view}", status_code=302)
    page = int(raw_page)
    status_filters = {"active": ["pending", "running", "failed"], "history": ["done"], "all": None}
    attention_filters = {"active": True, "history": False, "all": None}
    from app.models.managers.job_manager import JobManager

    rows, total = await JobManager().page_for_web(
        tenant_id,
        statuses=status_filters[view],
        page=page,
        page_size=HISTORY_LIMIT,
        include_attention=attention_filters[view],
    )

    stats, signals = await JobManager().overview_for_web(tenant_id)
    retention_policy = None
    if can_manage_retention and tenant_id is not None:
        from app.models.scheduling.agent_task import AgentTask
        from app.jobs.lifecycle import retention_policy as parse_retention_policy

        with tenant_scope(tenant_id):
            prune_tasks = await AgentTask.objects.filter(job_type="prune", is_active=True).order_by(
                AgentTask.updated_at.desc(), AgentTask.id.desc()
            )
        for prune_task in prune_tasks:
            values = parse_retention_policy(prune_task.payload)
            if values:
                retention_policy = {"name": prune_task.name, "success_hours": values[0], "failure_hours": values[1]}
                break
    total_pages = max(1, (total + HISTORY_LIMIT - 1) // HISTORY_LIMIT)
    if page > total_pages:
        page = total_pages
        rows, total = await JobManager().page_for_web(
            tenant_id,
            statuses=status_filters[view],
            page=page,
            page_size=HISTORY_LIMIT,
            include_attention=attention_filters[view],
        )

    # Sources each job touched, so `#1234` also answers "по каким источникам".
    if is_superuser and tenant_id is None:
        with tenant_scope(bypass=True):
            sources_map = await _job_sources(rows)
    else:
        sources_map = await _job_sources(rows)

    from app.web.tasks import _run_outcome

    job_outcomes = {job.id: _run_outcome(job.job_type, job.result or {}) for job in rows if job.status == "done"}
    for job in rows:
        if _job_has_attention(job):
            job_outcomes[job.id] = "partial"

    return render(
        request,
        "web/jobs.html",
        section="jobs",
        jobs=rows,
        view=view,
        page=page,
        total_pages=total_pages,
        total_filtered=total,
        sources_map=sources_map,
        job_outcomes=job_outcomes,
        job_diagnostics={
            job.id: _safe_job_diagnostic(job)
            for job in rows
            if job.status == "failed" or job_outcomes.get(job.id) == "partial"
        },
        unconfirmed_jobs={job.id for job in rows if _job_requires_review(job)},
        stats=stats,
        signals=signals,
        overdue_pending_minutes=OVERDUE_PENDING_MINUTES,
        stale_running_minutes=STALE_RUNNING_MINUTES,
        retryable={job.id for job in rows if _replayable_job(job)},
        replay_block_reasons={
            job.id: replay_block_reason(job)
            for job in rows
            if job.status in {"done", "failed"} and not _replayable_job(job)
        },
        deletable=DELETABLE,
        cancellable=CANCELLABLE,
        is_superuser=is_superuser,
        can_manage_retention=can_manage_retention,
        retention_policy=retention_policy,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
    )


@router.post("/clear")
async def jobs_clear(
    request: Request,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Drop the whole queue history of one workspace.

    Two deliberate limits:

    * **One workspace at a time.** `action_tenant_id` returns None for a
      superuser with no active workspace, and "delete every job of every
      workspace" is not a button anyone should be one misclick away from — so a
      superuser has to pick a workspace in the filter first.
    * **`running` rows survive.** A claimed row belongs to the worker until it
      reports back (see the module docstring); the rest is history, and history
      is what grows without bound.

    The count in the flash is the number of rows actually removed, and the
    number left behind is stated too — a «очистил всё» that silently kept the
    running job would read as a bug.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/jobs", status_code=302)

    gated = guard_web(request, "job", "delete", back="/app/")
    if gated is not None:
        return gated

    denied = guard_web(request, "agenttask", "delete", back="/app/jobs")
    if denied is not None:
        return denied

    tenant_id = action_tenant_id(request, tenant_id)
    if tenant_id is None:
        add_flash(request, "error", "Выберите пространство — очистка очереди по всем сразу недоступна")
        return RedirectResponse("/app/jobs", status_code=302)

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        # The tenant is an explicit filter, not just the manager's guard: the guard
        # is what keeps the *page* honest, and a mutation that trusted it alone
        # would be one refactor away from deleting another workspace's row.
        total = await Job.objects.filter(tenant_id=tenant_id).count()
        safe_error, safe_result = _safe_terminal_delete_criteria()
        deleted = await Job.objects.filter(
            safe_error,
            safe_result,
            tenant_id=tenant_id,
            status__in=["done", "failed"],
        ).delete()
        kept = total - deleted

    noun = plural(deleted, "задание", "задания", "заданий")
    text = f"Очищено: {deleted} {noun}"
    if kept:
        text += f", выполняющихся оставлено — {kept}"
    add_flash(request, "success", text)
    return RedirectResponse("/app/jobs", status_code=302)


@router.post("/{job_id}/delete")
async def job_delete(
    request: Request,
    job_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Delete one job row — the per-row trash button.

    Same two limits as `jobs_clear`, resolved per row: an explicit workspace for
    a superuser, and no deleting a `running` row (the worker owns it until it
    reports back). A job of another workspace is not reachable at all — the
    lookup filters on `tenant_id` explicitly, not only through `tenant_scope`.

    The read above can go stale in the window before the delete: the row may be
    reclaimed (back to `running`) or disappear. The DELETE is therefore
    conditional on the observed row — id, tenant and a still-deletable status —
    and reports success only when exactly one row was actually removed. Database
    errors propagate untouched; the delete is never repeated.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/jobs", status_code=302)

    gated = guard_web(request, "job", "delete", back="/app/")
    if gated is not None:
        return gated

    denied = guard_web(request, "agenttask", "delete", back="/app/jobs")
    if denied is not None:
        return denied

    tenant_id = action_tenant_id(request, tenant_id)

    from app.core.tenant_context import tenant_scope

    # `bypass` only for the superuser-without-workspace case: there is no
    # tenant to scope to, and the row is addressed by its own id, so the delete
    # still lands on exactly one row.
    with tenant_scope(tenant_id, bypass=tenant_id is None):
        # Same rule as `jobs_clear`: the workspace is an explicit filter, so the
        # handler's own boundary does not rest on the manager guard alone.
        filters: dict = {"id": job_id} if tenant_id is None else {"id": job_id, "tenant_id": tenant_id}
        job = await Job.objects.get(**filters)
        if job is None:
            add_flash(request, "error", "Задание не найдено")
            return RedirectResponse("/app/jobs", status_code=302)

        if job.status not in DELETABLE:
            add_flash(request, "error", f"Задание #{job.id} выполняется — его нельзя удалить")
            return RedirectResponse("/app/jobs", status_code=302)
        # The delete repeats the observed row's identity, tenant and a still-
        # deletable status as conditions, not a pre-check alone: if the row
        # changed since the read — reclaimed, rebound or gone — it hits zero rows.
        deleted = await Job.objects.filter(
            id=job.id,
            tenant_id=job.tenant_id,
            status__in=sorted(DELETABLE),
        ).delete()

    if deleted == 1:
        add_flash(request, "success", f"Задание #{job_id} удалено")
    else:
        add_flash(request, "error", f"Задание #{job_id} не удалено: состояние изменилось. Проверьте его.")
    return RedirectResponse("/app/jobs", status_code=302)


@router.post("/{job_id}/run")
async def job_run(
    request: Request,
    job_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Create a new replay job while preserving the original outcome row."""
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/jobs", status_code=302)

    gated = guard_web(request, "job", "update", back="/app/")
    if gated is not None:
        return gated

    tenant_id = action_tenant_id(request, tenant_id)

    # Executing a job runs a handler that may collect, analyse or send — the
    # same right the tasks page requires for "run now".
    denied = guard_web(request, "agenttask", "update", back="/app/jobs")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        job = await Job.objects.get(id=job_id, tenant_id=tenant_id)
        if job is None:
            add_flash(request, "error", "Задание не найдено")
            return RedirectResponse("/app/jobs", status_code=302)

        if not _replayable_job(job):
            add_flash(request, "error", "Этот запуск нельзя безопасно повторить. Проверьте сохранённый результат и настройки задачи.")
            return RedirectResponse("/app/jobs", status_code=302)
        actor_id = getattr(getattr(request.state, "web_user", None), "id", None)
        try:
            replay = await Job.objects.enqueue_replay(job.id, initiated_by_user_id=actor_id)
        except ValueError as error:
            add_flash(request, "error", str(error))
            return RedirectResponse("/app/jobs", status_code=302)
    from app.jobs.dispatcher import launch_job_background

    launch_job_background(replay.id, allow_retry=False)
    add_flash(request, "success", f"Создан повторный запуск #{replay.id}; исходный job #{job.id} сохранён")
    return RedirectResponse("/app/jobs", status_code=302)


@router.post("/{job_id}/cancel")
async def job_cancel(
    request: Request,
    job_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Record cancellation only for the running generation observed here.

    A committed cancellation fences later claimed outcomes, not an in-flight
    provider call. It does not prove that the worker or external effects stopped.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/jobs", status_code=302)

    gated = guard_web(request, "job", "update", back="/app/")
    if gated is not None:
        return gated

    denied = guard_web(request, "agenttask", "delete", back="/app/jobs")
    if denied is not None:
        return denied

    tenant_id = action_tenant_id(request, tenant_id)

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id, bypass=tenant_id is None):
        filters: dict = {"id": job_id} if tenant_id is None else {"id": job_id, "tenant_id": tenant_id}
        job = await Job.objects.get(**filters)
        if job is None:
            add_flash(request, "error", "Задание не найдено")
            return RedirectResponse("/app/jobs", status_code=302)

        if job.status != "running":
            add_flash(request, "error", f"Задание #{job.id} уже {job.status} — прервать нечего")
            return RedirectResponse("/app/jobs", status_code=302)

        from app.jobs.claim_outcomes import JobClaim

        try:
            observed = JobClaim.capture(job)
        except ValueError:
            add_flash(request, "error", "Отмена не подтверждена: данные запуска неполны. Проверьте состояние задания.")
            return RedirectResponse("/app/jobs", status_code=302)
        cancelled = await Job.objects.cancel_running(observed)
        if not cancelled:
            add_flash(request, "error", "Отмена не выполнена: состояние запуска изменилось. Проверьте задание.")
            return RedirectResponse("/app/jobs", status_code=302)

    add_flash(request, "success", f"Отмена задания #{job_id} записана. Выполнение внешних действий могло продолжиться.")
    return RedirectResponse("/app/jobs", status_code=302)
