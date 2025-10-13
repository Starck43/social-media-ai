from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.types.models import UserRoleType, ActionType
from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label

if TYPE_CHECKING:
    from . import Role


@app_label("account")
class User(Base, TimestampMixin):
    __tablename__ = 'users'
    __table_args__ = {'schema': settings.DB_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=True)

    # Relationship to Role
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("social_manager.roles.id"), nullable=False)
    role: Mapped["Role"] = relationship("Role", back_populates="users")

    # Manager will be set after class definition to avoid circular imports
    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager
        from .managers.user_manager import UserManager
        objects: ClassVar[UserManager | BaseManager]
    else:
        objects: ClassVar = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._permissions_cache = None

    def has_perm(self, permission: ActionType) -> bool:
        """Check if user has a specific permission"""
        if self.is_superuser:
            return True

        if self._permissions_cache is None:
            self._permissions_cache = {p.codename for p in self.role.permissions}

        return permission in self._permissions_cache

    def __str__(self) -> str:
        return f"{self.username}"

    def has_role(self, role: UserRoleType) -> bool:
        """Check if user has a specific role"""
        return self.role.codename == role.name

    def has_minimum_role(self, min_role: UserRoleType) -> bool:
        """Check if user has at least the specified role in hierarchy"""
        role_hierarchy = list(UserRoleType)
        try:
            user_level = role_hierarchy.index(UserRoleType[self.role.codename.name])
            min_level = role_hierarchy.index(min_role)
            return user_level >= min_level
        except (ValueError, KeyError):
            return False


from .managers.user_manager import UserManager  # noqa: E402
User.objects = UserManager()
