"""Legacy AIAnalytics imports and unchanged manager-binding entry point."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import JSON, Column, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from ..types import PeriodType
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from . import Source

from .analysis.ai_analytics import AIAnalytics

from .managers.ai_analytics_manager import AIAnalyticsManager  # noqa: E402

AIAnalytics.objects = AIAnalyticsManager()
