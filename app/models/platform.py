"""Compatibility aliases; canonical declarations live in domain packages."""

from .collection.platform import Platform
from .managers.platform_manager import PlatformManager

__all__ = ["Platform", "PlatformManager"]
