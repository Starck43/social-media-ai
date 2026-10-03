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
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models.job import Job

from .deps import action_tenant_id, add_flash, ensure_csrf, guard_web, render, tenant_filter_context

router = APIRouter(prefix="/jobs")

# Enough rows to spot a stuck or failing schedule without turning the page into
# a log viewer; the queue is a status screen, and `/app/tasks` is the place to
# manage the recurring work itself.
HISTORY_LIMIT = 50

# Statuses a re-run is meaningful for. `pending` already runs on its own, and
# `running` is claimed (see the module docstring).
RETRYABLE = frozenset({"failed"})


async def _jobs_for(tenant_id: int | None) -> list[Job]:
    """Most recent jobs, newest first, for one workspace (None = all)."""
    query = Job.objects.order_by(Job.created_at.desc()).limit(HISTORY_LIMIT)
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    return list(await query)


def _stats(rows: list[Job]) -> dict[str, int]:
    """Queue health. `failed` and `pending` are the two numbers that matter:
    pending says the scheduler will get to it, failed says it needs a human."""
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

    return render(
        request,
        "web/jobs.html",
        section="jobs",
        jobs=rows,
        stats=_stats(rows),
        retryable=RETRYABLE,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
    )


@router.post("/{job_id}/run")
async def job_run(
    request: Request,
    job_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Re-execute one job row now, synchronously (the dispatcher's claim path)."""
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/jobs", status_code=302)

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

    from app.jobs.dispatcher import run_job_now

    result = await run_job_now(job.id, allow_retry=False)
    if result and result.get("status") == "failed":
        add_flash(request, "error", f"Задание снова упало: {result.get('error', '?')}")
    else:
        add_flash(request, "success", "Задание выполнено")
    return RedirectResponse("/app/jobs", status_code=302)
