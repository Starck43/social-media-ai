"""Compatibility aliases; canonical declarations live in domain packages."""

from .collection.source import Source
from .managers.source_manager import SourceManager

__all__ = ["Source", "SourceManager"]
