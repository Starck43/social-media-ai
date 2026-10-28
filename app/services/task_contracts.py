"""Strict boundary contracts for bounded task-plan launches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

TASK_REQUEST_ID_PATTERN = r"^[A-Za-z0-9_.:-]{8,128}$"
_TASK_REQUEST_ID = re.compile(TASK_REQUEST_ID_PATTERN, re.ASCII)

# Bounded one-off HTTP admissions. The workspace limit follows its task-plan
# ceiling (up to 20); actor concurrency stays small regardless of tier.
MAX_ACTIVE_TASK_LAUNCHES_PER_ACTOR = 3
MAX_ACTIVE_TASK_LAUNCHES_PER_WORKSPACE = 20


class TaskLaunchArguments(BaseModel):
    """Transport-neutral launch arguments; the legacy API adapts into this shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(
        pattern=TASK_REQUEST_ID_PATTERN,
        strict=True,
        description="Stable key for this launch intent; reuse it only for the same conditions.",
    )
    plan: dict[str, Any] = Field(
        description="Versioned task plan to execute once; recurring schedules require a separate confirmation."
    )
    job_type: Literal["collect", "analyze"] = Field(
        default="analyze",
        description="Whether to collect permitted content only or collect and analyze it.",
    )

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        """Use ASCII full-match semantics, including for a trailing newline."""
        if not isinstance(value, str) or _TASK_REQUEST_ID.fullmatch(value) is None:
            raise ValueError("request_id must contain 8–128 supported ASCII characters")
        return value

    def to_command(self, *, job_type: str | None = None) -> "TaskLaunchCommand":
        """Build an immutable use-case command after strict envelope validation."""
        job_type = self.job_type if job_type is None else job_type
        if job_type not in {"collect", "analyze"}:
            raise ValueError("Unsupported social task job type")
        return TaskLaunchCommand(request_id=self.request_id, plan=self.plan, job_type=job_type)


@dataclass(frozen=True)
class TaskLaunchCommand:
    """Validated request fields passed into task launch orchestration."""

    request_id: str
    plan: Mapping[str, Any]
    job_type: Literal["collect", "analyze"] = "analyze"


TASK_LAUNCH_ERROR_MESSAGES = {
    "request_invalid": (400, "The task launch request is invalid."),
    "launch_forbidden": (403, "Task launch is not permitted for this workspace member."),
    "schedule_confirmation_required": (400, "Recurring tasks require explicit schedule confirmation."),
    "scenario_required": (400, "Select an analysis scenario before launching."),
    "scenario_unavailable": (400, "Scenario is unavailable in this workspace"),
    "source_unavailable": (400, "Selected sources are inactive or outside the task's platform/workspace"),
    "workspace_task_limit": (400, "Workspace task limit reached; review your tasks or plan"),
    "actor_task_limit": (429, "Too many active task launches for this workspace member; retry shortly."),
    "workspace_admission_limit": (429, "This workspace has reached its active task limit; retry shortly."),
    "request_id_conflict": (400, "request_id was already used for different task conditions"),
    "source_busy": (409, "A source in this plan already has an active job."),
    "billing_unavailable": (409, "Task was saved inactive; configure personal billing before retrying this request."),
    "launch_unconfirmed": (500, "The launch result is unconfirmed; inspect the task before retrying."),
}
TASK_LAUNCH_ERROR_CODES = frozenset(TASK_LAUNCH_ERROR_MESSAGES)


class TaskLaunchError(ValueError):
    """A fixed, safe task-launch rejection mapped by each transport boundary."""

    def __init__(self, code: str, *, cause: BaseException | None = None) -> None:
        if code not in TASK_LAUNCH_ERROR_MESSAGES:
            raise ValueError("Unknown task launch error code")
        self.code = code
        self.status_code, self.safe_message = TASK_LAUNCH_ERROR_MESSAGES[code]
        super().__init__(self.safe_message)
        if cause is not None:
            self.__cause__ = cause


@dataclass(frozen=True)
class TaskLaunchResult:
    """Allowlisted receipt fields; never carries a task, job or tenant ORM row."""

    task_id: int
    job_id: int | None
    status: Literal["queued", "replayed", "unconfirmed"]
    job_status: Literal["pending", "running", "done", "failed", "unknown", "unavailable"]


def present_task_launch_result(result: TaskLaunchResult) -> dict[str, object]:
    """Project the stable legacy receipt without exposing ORM/provider fields."""
    if type(result.task_id) is not int or result.task_id <= 0:
        raise ValueError("Invalid task launch receipt")
    job_id = result.job_id if type(result.job_id) is int and result.job_id > 0 else None
    status = result.status if result.status in {"queued", "replayed"} else "unconfirmed"
    job_status = (
        result.job_status
        if result.job_status in {"pending", "running", "done", "failed", "unknown", "unavailable"}
        else "unknown"
    )
    return {
        "task_id": result.task_id,
        "job_id": job_id,
        "status": status,
        "job_status": job_status,
        "task_url": f"/app/tasks/{result.task_id}",
    }


def task_launch_tool_error(code: str) -> dict[str, str]:
    """Present only allowlisted launch errors; unknown failures are unconfirmed."""
    if code not in TASK_LAUNCH_ERROR_MESSAGES:
        code = "launch_unconfirmed"
    return {"error": TASK_LAUNCH_ERROR_MESSAGES[code][1], "code": code}


def task_plan_tool_error(code: str) -> dict[str, str]:
    """Present parser errors using their bounded internal codes only."""
    from app.services.task_plans import TaskPlanError

    safe_code = TaskPlanError(code).code
    return {"error": f"Invalid task plan ({safe_code}).", "code": safe_code}
