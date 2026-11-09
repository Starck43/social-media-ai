"""External calendar schedule authority at planner reminder and Today boundaries."""

from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app.services.planner.reminder_contracts import ReminderCreate
from app.services.planner.reminders import reminder_time
from app.services.planner.tasks import PlannerConflict
from app.services.workspace.today import TodayProjection


class PlannerPermissions:
    workspace_role = "MEMBER"

    def can(self, resource, action="view"):
        return (resource, action) in {("plannertask", "view"), ("planner.task", "view")}


class PlannerRepository:
    def __init__(self, task, schedule):
        self.task = task
        self.schedule = schedule

    async def planner_tasks(self, workspace_id, limit):
        return [self.task]

    async def planner_schedule(self, task_id):
        return self.schedule


@pytest.mark.parametrize("preset", ["day", "week", "month"])
def test_external_calendar_schedule_rejects_relative_reminder_presets(preset):
    task = NS(recurrence_source="calendar", due_at=None, due_date=date(2020, 1, 1), timezone="UTC")

    with pytest.raises(PlannerConflict, match="calendar_relative_reminder_requires_provider"):
        reminder_time(ReminderCreate(preset=preset), task, now=datetime(2026, 11, 9, tzinfo=timezone.utc))


def test_external_calendar_schedule_accepts_explicit_custom_reminder_time():
    task = NS(recurrence_source="calendar", due_at=None, due_date=date(2020, 1, 1), timezone="UTC")
    remind_at = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)

    result = reminder_time(
        ReminderCreate(preset="custom", remind_at=remind_at),
        task,
        now=datetime(2026, 11, 9, tzinfo=timezone.utc),
    )

    assert result == remind_at


@pytest.mark.asyncio
async def test_today_filters_on_live_provider_due_date_and_never_labels_stale_local_date_overdue():
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    stale_task = NS(
        id=41,
        tenant_id=7,
        owner_user_id=10,
        assignee_id=None,
        status="todo",
        due_date=date(2020, 1, 1),
        due_at=None,
        title_encrypted="Calendar task",
        recurrence_source="calendar",
    )
    future_provider_schedule = NS(due_date=date(2031, 5, 6), due_at=None)
    projection = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=PlannerPermissions(),
        repository=PlannerRepository(stale_task, future_provider_schedule),
        now=now,
    )

    future_section = await projection.build_today_plan_section()

    assert future_section.items == []

    projection.repo.schedule = None
    unavailable_section = await projection.build_today_plan_section()

    assert len(unavailable_section.items) == 1
    item = unavailable_section.items[0]
    assert item["stale_or_unavailable"] is True
    assert item["status_label"] == "Источник недоступен"
    assert item["summary"] == "В плане к выполнению"
    assert item["created_at"] == ""
