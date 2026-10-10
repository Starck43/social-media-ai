"""Legacy Role imports with unchanged runtime binding semantics."""

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import String, Text, Integer, Table, Column, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base, TimestampMixin
from ..core.config import settings
from ..types import UserRoleType

if TYPE_CHECKING:
    from . import User, Permission


# Table for many-to-many relationship between Role and Permission

# isort: split
from .identity.role import Role, role_permission

# isort: split
from .managers.role_manager import RoleManager  # noqa: E402

Role.objects = RoleManager(Role)
