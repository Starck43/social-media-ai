"""Compatibility aliases; canonical declarations live in domain packages."""

from ..types import SourceType
from .base import Base, TenantScopedMixin, TimestampMixin
from .collection.source import Source
from .managers.source_manager import SourceManager

__all__ = ["Base", "Source", "SourceManager", "SourceType", "TenantScopedMixin", "TimestampMixin"]
