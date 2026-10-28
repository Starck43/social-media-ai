"""Shared dependencies and router for the task web surface."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from app.jobs.progress import read_snapshot
from app.jobs.recovery_policy import CANCELLED_BEFORE_START_ERROR, OPERATOR_CANCELLED_ERROR, outcome_unconfirmed
from app.models import AgentScenario, AgentTask, Job, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.services.collection.intent import (
    CITY_MODES,
    CITY_PREFER,
    COLLECTION_JOB_TYPES,
    CONFIRM_MODES,
    CollectionIntent,
    CollectionIntentError,
    apply_collection,
    collection_error_message,
    parse_collection_intent,
)
from app.services.monitoring.selection import SelectionError
from app.tasks.cron import cron_to_human
from app.types import AgentActionType, BotTriggerType, JobType
from app.web.task_progress import project_run, sidebar_sources
from .deps import (
    action_tenant_id,
    add_flash,
    ensure_csrf,
    guard_web,
    perms_can,
    plural,
    render,
    safe_next,
    tenant_filter_context,
)

router = APIRouter(prefix="/tasks")
logger = logging.getLogger("app.web.tasks")
__all__ = [name for name in globals() if not name.startswith("__")]
