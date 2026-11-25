"""Canonical work-task contracts for the v2 API, the CLI and the agent tools.

The DTOs deliberately reuse the service schemas (`CanonicalTaskOut`, `CreateTask`,
`UpdateTask`) instead of redeclaring fields: one source of truth keeps the three
surfaces from drifting away from the permission and version rules the services
enforce.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field, StrictInt

from app.schemas.business import BusinessSchema
from app.services.planner.canonical_tasks import CanonicalTaskOut, Completion, CreateTask, UpdateTask
from app.services.planner.contracts import DeletedOut, TaskVersion

WorkTaskCreate = CreateTask
WorkTaskUpdate = UpdateTask
WorkTaskCompletion = Completion
WorkTaskVersion = TaskVersion
WorkTaskDeleted = DeletedOut

DecisionSource = Literal["local", "command", "document"]
ActionReference = Literal["edit", "complete", "run", "delete", "close", "reopen", "schedule"]
ClosureOperation = Literal["close", "reopen"]


class WorkTaskAction(BusinessSchema):
    reference: ActionReference
    label: str = Field(min_length=1, max_length=80)
    revision: StrictInt | None = Field(default=None, gt=0)
    confirmation_required: bool | None = None
    enabled: bool | None = None


class LatestRun(BusinessSchema):
    run_id: StrictInt = Field(gt=0)
    status: str = Field(min_length=1, max_length=32)


class PendingDecision(BusinessSchema):
    reference: str = Field(min_length=3, max_length=96)
    source: DecisionSource
    label: str = Field(min_length=1, max_length=80)
    choices: list[Literal["accepted", "rejected"]] = Field(min_length=1, max_length=4)
    expires_at: datetime
    reason: str | None = Field(default=None, max_length=2000)
    target_revision: int | str | None = None
    href: str | None = Field(default=None, max_length=500)


class WorkTaskActions(BusinessSchema):
    task_id: StrictInt = Field(gt=0)
    version: StrictInt = Field(gt=0)
    work_status: str = Field(min_length=1, max_length=32)
    latest_run: LatestRun | None = None
    next_due_at: datetime | None = None
    actions: list[WorkTaskAction] = Field(max_length=8)
    pending_decisions: list[PendingDecision] = Field(max_length=20)


class RunReceipt(BusinessSchema):
    run_id: StrictInt = Field(gt=0)
    task_id: StrictInt = Field(gt=0)
    revision: StrictInt = Field(gt=0)
    job_id: StrictInt | None = Field(default=None, gt=0)
    status: str = Field(min_length=1, max_length=32)
    result: dict | None = None
    replayed: bool = False


class RunReceiptPage(BusinessSchema):
    items: list[RunReceipt] = Field(max_length=100)
    limit: StrictInt = Field(ge=1, le=100)
    next_before_id: StrictInt | None = Field(default=None, gt=0)


class PlanDraft(BusinessSchema):
    """Raw execution plan validated by the same parser the runtime uses."""

    plan: dict


class PlanPreview(BusinessSchema):
    task_id: StrictInt = Field(gt=0)
    task_version: StrictInt = Field(gt=0)
    operations: list[str] = Field(min_length=1, max_length=32)
    scheduled: bool
    dry_run: bool


class DeleteScope(BusinessSchema):
    scope: Literal["occurrence", "series"]


class DeleteConfirmation(DeleteScope):
    confirmation: str = Field(min_length=16, max_length=4096)


class DeleteEffects(BusinessSchema):
    scope: Literal["occurrence", "series"]
    occurrences: StrictInt = Field(ge=0)
    enabled_schedules: StrictInt = Field(ge=0)
    queued_jobs: StrictInt = Field(ge=0)
    running_jobs: StrictInt = Field(ge=0)
    retained_run_receipts: StrictInt = Field(ge=0)
    child_links_hidden: StrictInt = Field(ge=0)
    next_occurrence: str | None = None


class DeletePreview(BusinessSchema):
    effects: DeleteEffects
    confirmation: str = Field(min_length=16, max_length=4096)
    expires_in: StrictInt = Field(gt=0)


class DeleteOutcome(BusinessSchema):
    effects: DeleteEffects
    in_flight_uncertain: StrictInt = Field(ge=0)


class ReopenChoice(BusinessSchema):
    """The explicit schedule choice; closing never re-arms anything."""

    reactivate_schedule: bool = False


class ClosureConfirmation(BusinessSchema):
    confirmation: str = Field(min_length=16, max_length=4096)


class ReopenConfirmation(ClosureConfirmation):
    """The reopening token plus the schedule choice it was issued for."""

    reactivate_schedule: bool = False


class ClosureEffects(BusinessSchema):
    operation: ClosureOperation
    scope: Literal["occurrence"]
    task_id: StrictInt = Field(gt=0)
    version: StrictInt = Field(gt=0)
    status: str = Field(min_length=1, max_length=32)
    target_status: str = Field(min_length=1, max_length=32)
    series_id: StrictInt = Field(gt=0)
    creates_occurrence: bool
    successor_task_id: StrictInt | None = Field(default=None, gt=0)
    successor_due: str | None = None
    schedules: StrictInt = Field(ge=0)
    enabled_schedules: StrictInt = Field(ge=0)
    reactivates_schedule: bool
    admitted_run_id: StrictInt | None = Field(default=None, gt=0)
    admitted_run_status: str | None = Field(default=None, max_length=32)
    retained_run_receipts: StrictInt = Field(ge=0)
    next_due_at: datetime | None = None


class ClosurePreview(BusinessSchema):
    effects: ClosureEffects
    confirmation: str = Field(min_length=16, max_length=4096)
    expires_in: StrictInt = Field(gt=0)


class ClosureOutcome(BusinessSchema):
    effects: ClosureEffects
    replayed: bool


class ScheduleRequest(BusinessSchema):
    enabled: bool
    revision: StrictInt = Field(gt=0)
    expected_version: StrictInt = Field(gt=0)


class ScheduleOutcome(BusinessSchema):
    task_id: StrictInt = Field(gt=0)
    revision: StrictInt = Field(gt=0)
    task_version: StrictInt = Field(gt=0)
    trigger_id: StrictInt | None = Field(default=None, gt=0)
    enabled: bool
    cron_expression: str | None = Field(default=None, max_length=100)
    timezone_name: str | None = Field(default=None, max_length=100)
    next_due_at: datetime | None = None


class DecisionRequest(BusinessSchema):
    reference: str = Field(min_length=3, max_length=96)
    outcome: Literal["accepted", "rejected"]


class DecisionPreview(DecisionRequest):
    confirmation: str = Field(min_length=16, max_length=4096)
    source: DecisionSource
    label: str = Field(min_length=1, max_length=80)
    target_revision: int | str | None = None


class DecisionConfirmation(BusinessSchema):
    confirmation: str = Field(min_length=16, max_length=4096)
    reason: str = Field(default="", max_length=2000)


class DecisionOutcome(BusinessSchema):
    reference: str
    outcome: str = Field(min_length=1, max_length=40)
    decided_at: datetime | None = None


class LegacyTarget(BusinessSchema):
    """Canonical target of an opaque legacy URL, resolved through its scoped mapping."""

    namespace: str = Field(min_length=1, max_length=64)
    legacy_id: StrictInt = Field(gt=0)
    task: CanonicalTaskOut
