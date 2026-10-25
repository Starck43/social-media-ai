"""Schemas for Task API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """Request body for creating a task."""

    name: str = Field(..., min_length=1, max_length=100)
    cron_expr: Optional[str] = Field(None, min_length=1)
    job_type: str = Field(..., pattern="^(collect|digest|prune|analyze|learn|reflect)$")
    payload: dict[str, Any] = Field(default_factory=dict)
    # Selection (`docs/design/task-selection.md`): top-level too, because they
    # are a task property, not an opaque blob. The API merges them into
    # `payload` through the same writer the web form and CLI use.
    tracked_users: Optional[list[str]] = Field(
        None, description="Authors whose items are analysed: platform:external_id. Empty = the sources' own items."
    )
    keywords_list: Optional[list[str]] = Field(None, description="Phrases an item must contain to be analysed.")
    keywords_match: Optional[str] = Field(None, pattern="^(any|all)$")
    collection: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Task-owned collection intent: content_types, engagement_types, limit, queries, city, "
            "discovery, comments, candidates, date_window. Omit to keep the payload's block."
        ),
    )
    task_plan: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Optional canonical social task plan: goal, platforms, explicit source_scope, collection, "
            "fixed/rolling window, scenario and once/schedule trigger. New plans must not encode scope "
            "implicitly through an empty source_ids list."
        ),
    )
    agent_scenario_id: Optional[int] = None
    source_ids: list[int] = Field(default_factory=list)
    is_active: bool = True


class TaskUpdate(BaseModel):
    """Request body for updating a task — only provided fields are changed."""

    name: Optional[str] = Field(None, max_length=100)
    cron_expr: Optional[str] = None
    job_type: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    tracked_users: Optional[list[str]] = Field(
        None, description="Authors whose items are analysed: platform:external_id. Empty list clears the filter."
    )
    keywords_list: Optional[list[str]] = Field(None, description="Phrases an item must contain.")
    keywords_match: Optional[str] = Field(None, pattern="^(any|all)$")
    collection: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Task-owned collection intent. Omit to keep the stored block; null clears it; "
            "an object replaces it and is validated through the shared parser."
        ),
    )
    task_plan: Optional[dict[str, Any]] = Field(
        None,
        description=("Optional canonical social task plan. Omit to preserve the existing plan."),
    )
    agent_scenario_id: Optional[int] = None
    source_ids: Optional[list[int]] = None
    is_active: Optional[bool] = None


class TaskRunRequest(BaseModel):
    """Request body for running a task immediately."""

    payload: Optional[dict[str, Any]] = None


class TaskPlanPreviewRequest(BaseModel):
    """A pure preview; accepting this body never saves or runs a task."""

    job_type: str = Field(..., pattern="^(collect|analyze)$")
    task_plan: dict[str, Any]


class TaskPlanPreviewResponse(BaseModel):
    """Canonical task input returned by the shared plan parser."""

    task_plan: dict[str, Any]
    collection: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class TaskPlanRunRequest(BaseModel):
    """Explicit social one-off launch; separate from the legacy query route."""

    job_type: str = Field("analyze", pattern="^(collect|analyze)$")
    task_plan: dict[str, Any]
    request_id: str = Field(..., min_length=1, max_length=128)


class TaskResponse(BaseModel):
    """Response body for a task."""

    id: int
    name: str
    cron_expr: str
    job_type: str
    payload: dict[str, Any]
    is_active: bool
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    agent_scenario_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
