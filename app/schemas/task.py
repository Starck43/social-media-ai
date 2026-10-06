"""Schemas for Task API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """Request body for creating a task."""

    name: str = Field(..., min_length=1, max_length=100)
    cron_expr: str = Field(..., min_length=1)
    job_type: str = Field(..., pattern="^(collect|digest|prune|analyze|learn|reflect)$")
    payload: dict[str, Any] = Field(default_factory=dict)
    agent_scenario_id: Optional[int] = None
    source_ids: list[int] = Field(default_factory=list)
    is_active: bool = True


class TaskUpdate(BaseModel):
    """Request body for updating a task — only provided fields are changed."""

    name: Optional[str] = Field(None, max_length=100)
    cron_expr: Optional[str] = None
    job_type: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    agent_scenario_id: Optional[int] = None
    source_ids: Optional[list[int]] = None
    is_active: Optional[bool] = None


class TaskRunRequest(BaseModel):
    """Request body for running a task immediately."""

    payload: Optional[dict[str, Any]] = None


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
