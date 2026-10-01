"""Onboarding `/app/onboarding` — the first source and the first schedule.

A fresh workspace opens on an empty dashboard; this page is the guided way out
of it. Both forms POST to the existing create endpoints (`/app/sources`,
`/app/tasks`) carrying `next=/app/onboarding`, so "create" still has exactly one
implementation and the wizard cannot drift from the list pages.

Deliberately a checklist rather than a numbered stepper: each step shows
whether it is already done (an active source exists / a schedule exists) and
offers its form until it is. Nothing blocks reading the rest of the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func

from app.models import AgentTask, Source
from app.tasks.cron import cron_to_human
from app.types import JobType

from .deps import render

router = APIRouter(prefix="/onboarding")

# The schedule presets the wizard offers, as real cron expressions — the create
# endpoint takes `cron_custom`, so the select can carry the value directly.
SCHEDULE_PRESETS: tuple[tuple[str, str], ...] = (
    ("0 9 * * *", "Каждый день в 09:00"),
    ("0 9 * * 1", "Каждый понедельник в 09:00"),
    ("0 */6 * * *", "Каждые 6 часов"),
    ("0 23 * * *", "Каждый день в 23:00"),
)


@router.get("")
@router.get("/")
async def onboarding(request: Request):
    """Checklist for a workspace that has no source or no schedule yet."""
    # No `tenant_id` predicate: the manager's guard already scopes the rows to
    # the workspace `TenantUIMiddleware` opened for this request.
    sources_count = await Source.objects.filter(is_active=True).values(func.count(Source.id)).scalar(0)
    tasks_count = await AgentTask.objects.values(func.count(AgentTask.id)).scalar(0)

    from app.models.platform import Platform
    from app.types import SourceType

    return render(
        request,
        "web/onboarding.html",
        section="onboarding",
        has_source=bool(sources_count),
        has_task=bool(tasks_count),
        platforms=await Platform.objects.order_by(Platform.id),
        source_types=list(SourceType),
        job_types=JobType.choices(),
        presets=SCHEDULE_PRESETS,
        cron_to_human=cron_to_human,
    )
