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

The whole section is gated to a platform role above ADMIN (`UserRoleType.SUPERUSER`):
the queue exposes every workspace's internals, so a workspace owner sees it only
when their platform role says so. `guard_superuser` runs on every route after the
CSRF check; the sidebar drops the item for everyone else — which is not the check.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models.job import Job

from .deps import action_tenant_id, add_flash, ensure_csrf, guard_superuser, guard_web, plural, render, tenant_filter_context

router = APIRouter(prefix="/jobs")

# Enough rows to spot a stuck or failing schedule without turning the page into
# a log viewer; the queue is a status screen, and `/app/tasks` is the place to
# manage the recurring work itself.
HISTORY_LIMIT = 50

# Statuses a re-run is meaningful for. `pending` already runs on its own, and
# `running` is claimed (see the module docstring).
RETRYABLE = frozenset({"failed"})

# Statuses a delete may touch. `running` is claimed by the worker *right now* —
# see the module docstring for why that row is off limits.
DELETABLE = frozenset({"pending", "done", "failed"})

# Statuses a cancel may touch. Only `running` — anything else is already
# terminal or not yet in flight.
CANCELLABLE = frozenset({"running"})


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
        entries = [
            {"source_id": p.get("source_id"), "name": p.get("name")}
            for p in per
            if p.get("source_id")
        ]
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
        for sid in ((job.payload or {}).get("source_ids") or []):
            if not str(sid).isdigit():
                continue
            sid = int(sid)
            if sid not in seen:
                entries.append({"source_id": sid, "name": names.get(sid, f"Источник #{sid}")})
                seen.add(sid)

    return by_job


async def _all_stats(tenant_id: int | None) -> dict[str, int]:
    """Queue health across the *entire* history, not just the last HISTORY_LIMIT rows."""
    query = Job.objects
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    rows = list(await query)
    return {
        "pending": sum(1 for j in rows if j.status == "pending"),
        "running": sum(1 for j in rows if j.status == "running"),
        "done": sum(1 for j in rows if j.status == "done"),
        "failed": sum(1 for j in rows if j.status == "failed"),
    }


@router.get("")
@router.get("/")
async def jobs_list(request: Request):
    """Job history for the active workspace, with the queue's health on top."""
    gated = guard_superuser(request, back="/app/")
    if gated is not None:
        return gated

    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)
    tenant_id = getattr(request.state, "tenant_id", None)

    from app.core.tenant_context import tenant_scope

    # Same rule as the dashboard and digests: a superuser with no active
    # workspace sees the global queue, an ordinary user never does.
    if is_superuser and tenant_id is None:
        with tenant_scope(bypass=True):
            rows = await _jobs_for(filter_tenant_id)
    else:
        rows = await _jobs_for(filter_tenant_id if is_superuser else tenant_id)

    # Sources each job touched, so `#1234` also answers "по каким источникам".
    if is_superuser and tenant_id is None:
        with tenant_scope(bypass=True):
            sources_map = await _job_sources(rows)
    else:
        sources_map = await _job_sources(rows)

    # The same outcome classification the run-now modal and the task page use,
    # so one run reads the same in all three places.
    from app.web.tasks import _run_outcome

    job_outcomes = {job.id: _run_outcome(job.job_type, job.result or {}) for job in rows if job.status == "done"}

    return render(
        request,
        "web/jobs.html",
        section="jobs",
        jobs=rows,
        sources_map=sources_map,
        job_outcomes=job_outcomes,
        stats=await _all_stats(filter_tenant_id if is_superuser else tenant_id),
        retryable=RETRYABLE,
        deletable=DELETABLE,
        cancellable=CANCELLABLE,
        is_superuser=is_superuser,
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

    gated = guard_superuser(request, back="/app/")
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
        kept = await Job.objects.filter(tenant_id=tenant_id, status="running").count()
        deleted = await Job.objects.filter(tenant_id=tenant_id).exclude(status="running").delete()

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
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/jobs", status_code=302)

    gated = guard_superuser(request, back="/app/")
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

        await Job.objects.delete(**filters)

    add_flash(request, "success", f"Задание #{job_id} удалено")
    return RedirectResponse("/app/jobs", status_code=302)


@router.post("/{job_id}/run")
async def job_run(
    request: Request,
    job_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Attempt the existing claim path and report only a confirmed outcome.

    This route does not re-arm failed rows or infer completion from a missing
    claim. A dispatcher error propagates rather than becoming a success flash;
    a lost claim is the one reported case and renders an explicit error, never
    completion and never a silent retry.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/jobs", status_code=302)

    gated = guard_superuser(request, back="/app/")
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

        if job.status not in RETRYABLE:
            # `claim_job` returns None for a non-pending row, so this would
            # otherwise be a silent no-op with no explanation.
            add_flash(request, "error", f"Задание уже {job.status} — перезапуск не требуется")
            return RedirectResponse("/app/jobs", status_code=302)

    from app.jobs.claim_outcomes import JobClaimLostError
    from app.jobs.dispatcher import run_job_now

    try:
        result = await run_job_now(job.id, allow_retry=False)
    except JobClaimLostError:
        # The claim moved on: report uncertainty, never success, never a retry.
        add_flash(
            request, "error", "Результат выполнения не подтверждён: захват задания потерян. Проверьте его состояние."
        )
        return RedirectResponse("/app/jobs", status_code=302)
    if result is None:
        add_flash(request, "error", "Запуск не начат: задание не удалось захватить. Проверьте его статус.")
    elif not isinstance(result, dict):
        add_flash(request, "error", "Результат запуска не подтверждён. Проверьте состояние задания.")
    elif result.get("status") == "failed":
        add_flash(request, "error", f"Задание снова упало: {result.get('error', '?')}")
    elif result.get("status") == "done":
        details = result.get("result")
        if isinstance(details, dict) and details.get("status") == "skipped":
            add_flash(request, "info", "Задание завершено со статусом «пропущено».")
        else:
            add_flash(request, "success", "Задание выполнено")
    else:
        add_flash(request, "error", "Выполнение задания не подтверждено. Проверьте его статус.")
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

    gated = guard_superuser(request, back="/app/")
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
