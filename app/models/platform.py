"""Compatibility aliases; canonical declarations live in domain packages."""

from ..types import PlatformType
from .base import Base
from .collection.platform import Platform
from .managers.platform_manager import PlatformManager

__all__ = ["Base", "Platform", "PlatformManager", "PlatformType"]
