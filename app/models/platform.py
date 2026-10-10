"""Compatibility entry point; canonical declaration is in models.collection.

Legacy imported symbols and manager binding stay here. The canonical leaf only
maps the class; its manager is created AFTER this module exports that class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from ..core.config import settings
from ..core.decorators import app_label
from ..types import PlatformType
from .base import Base

if TYPE_CHECKING:
    from . import Source


from .collection.platform import Platform


from .managers.platform_manager import PlatformManager  # noqa: E402

Platform.objects = PlatformManager()
