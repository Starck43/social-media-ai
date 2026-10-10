"""Legacy User imports with unchanged runtime binding semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.types import UserRoleType, ActionType
from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label

if TYPE_CHECKING:
    from . import Role

# isort: split
from .identity.user import User

# isort: split
from .managers.user_manager import UserManager  # noqa: E402

User.objects = UserManager()
