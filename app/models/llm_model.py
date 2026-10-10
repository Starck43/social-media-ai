"""Legacy LLMModel imports and unchanged manager-binding entry point."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TimestampMixin

from .analysis.llm_model import LLMModel, _model_type_to_capabilities

from .managers.llm_model_manager import LLMModelManager  # noqa: E402

LLMModel.objects = LLMModelManager()
