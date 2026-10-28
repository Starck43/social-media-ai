"""Shared SQLAdmin dependencies for aggregate view modules."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Union
from fastapi import HTTPException
from sqladmin import action, expose
from sqladmin.fields import SelectField
from sqladmin.forms import get_model_form
from sqlalchemy import Select
from starlette.requests import Request
from starlette.responses import RedirectResponse
from wtforms import FloatField, Form, validators, widgets
from app.admin.actions import LLMModelActions
from app.admin.formatters import format_date, format_usd, model_price, plan_badge
from app.core.hashing import get_password_hash
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.provider_uid import slugify_uid
from app.models import (
    AgentScenario,
    AgentTask,
    AIAnalytics,
    BotAction,
    CollectedItem,
    LLMModel,
    LLMProvider,
    Notification,
    Permission,
    Platform,
    PlatformRole,
    Source,
    Tenant,
    User,
    UserCredential,
)
from app.models.managers.user_manager import UserManager
from app.tasks.cron import cron_to_human
from app.types import (
    ActionType,
    AnalysisType,
    BotActionStatus,
    JobType,
    LLMStrategyType,
    MediaType,
    NotificationType,
    PeriodType,
    PlatformRoleType,
    PlatformType,
    SourceType,
)
from app.types.enums.llm_types import APIFormatType
from app.services.ai.llm_client import LLMClientFactory
from app.admin.base import BaseAdmin
from app.admin.catalog import PermissionLabelAdmin, PlatformRoleGrantAdmin
from app.admin.filtered_select import FilteredSelectMultipleField
from app.admin.price_fields import ExactModelPriceField
from app.admin.role_picker import RoleChoiceField, RolePickerConverter, role_choices, role_edit
from app.admin.widgets import CommaSeparatedSelectMultipleField, EuropeanDateField
from app.models import Job

logger = logging.getLogger(__name__)
__all__ = [name for name in globals() if not name.startswith("__")]
