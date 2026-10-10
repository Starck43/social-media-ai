"""Legacy LLMProvider imports and unchanged manager-binding entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, Column, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TimestampMixin

from .analysis.llm_provider import LLMProvider

from .managers.llm_provider_manager import LLMProviderManager  # noqa: E402

LLMProvider.objects = LLMProviderManager()
