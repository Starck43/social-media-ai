"""Legacy imports; canonical model and manager registration live in the domain facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.types import ActionType, PlatformRoleType

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from . import Role

# isort: split
from .identity.user import User

# isort: split
from .managers.user_manager import UserManager  # noqa: E402
