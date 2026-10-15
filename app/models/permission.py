"""Legacy imports; canonical model and manager registration live in the domain facade."""

import re
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.types import ActionType

from ..core.config import settings
from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from . import ModelType

# isort: split
from .identity.permission import Permission

# isort: split
from .managers.permission_manager import PermissionManager  # noqa: E402
