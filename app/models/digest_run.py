"""Legacy DigestRun imports and unchanged manager-binding entry point."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Column, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.digest_run_manager import DigestRunManager

# isort: split
from .scheduling.digest_run import DigestRun

# isort: split
from .managers.digest_run_manager import DigestRunManager  # noqa: E402

DigestRun.objects = DigestRunManager()
