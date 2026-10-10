"""Legacy Permission imports with unchanged runtime binding semantics."""

import re
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Integer, String, UniqueConstraint, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.types import ActionType
from .base import Base, TimestampMixin
from ..core.config import settings

if TYPE_CHECKING:
    from . import ModelType

# isort: split
from .identity.permission import Permission

# isort: split
from .managers.permission_manager import PermissionManager  # noqa: E402

Permission.objects = PermissionManager()
