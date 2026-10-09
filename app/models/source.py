"""Compatibility entry point; canonical declaration is in models.collection.

Legacy imported symbols and manager binding stay here. The canonical leaf only
maps the class; its manager is created AFTER this module exports that class.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
	JSON,
	Boolean,
	DateTime,
	ForeignKey,
	Index,
	Integer,
	String,
	UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from ..core.config import settings
from ..core.decorators import app_label
from ..types import SourceType
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
	from . import AIAnalytics, Platform, Tenant
	from .managers.source_manager import SourceManager


from .collection.source import Source


from .managers.source_manager import SourceManager  # noqa: E402

Source.objects = SourceManager()
