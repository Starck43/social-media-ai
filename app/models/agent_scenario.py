"""Legacy AgentScenario imports and unchanged manager-binding entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from ..types import LLMStrategyType
from .base import Base, TenantScopedMixin, TimestampMixin

from .analysis.agent_scenario import AgentScenario

from .managers.agent_scenario_manager import AgentScenarioManager  # noqa: E402

AgentScenario.objects = AgentScenarioManager()
