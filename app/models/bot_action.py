"""Legacy BotAction imports and unchanged manager-binding entry point."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from ..types import BotActionStatus, AgentActionType
from .base import Base, TenantScopedMixin, TimestampMixin

# isort: split
from .scheduling.bot_action import BotAction

# isort: split
from .managers.bot_action_manager import BotActionManager  # noqa: E402

BotAction.objects = BotActionManager()
