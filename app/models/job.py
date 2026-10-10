"""Legacy Job imports and unchanged manager-binding entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.job_manager import JobManager

# isort: split
from .scheduling.job import Job

# isort: split
from .managers.job_manager import JobManager  # noqa: E402

Job.objects = JobManager()
