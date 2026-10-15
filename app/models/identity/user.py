from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.types import ActionType, PlatformRoleType

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

if TYPE_CHECKING:
    from .. import Role


@app_label("identity")
class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationship to Role
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{settings.DB_SCHEMA}.roles.id"), nullable=False)
    role: Mapped["Role"] = relationship("PlatformRole", back_populates="users")

    # Manager will be set after class definition to avoid circular imports
    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.user_manager import UserManager

        objects: ClassVar[UserManager | BaseManager]
    else:
        objects: ClassVar = None

    # Class-level default so the cache exists even on ORM-loaded instances
    # (SQLAlchemy populates via __new__, skipping __init__).
    _model_perms_cache: set[tuple[str, ActionType]] | None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model_perms_cache = None

    @property
    def is_platform_admin(self) -> bool:
        return bool(self.is_active and self.role and self.role.codename == PlatformRoleType.ADMIN)

    def _is_superuser_role(self) -> bool:
        """Compatibility predicate for platform entrypoints only."""
        return self.is_platform_admin

    def model_permissions(self, model_name: str) -> set[ActionType]:
        """Registered actions granted to this platform operator for one resource.

        Stable resource keys define authority; model names are lookup metadata.
        Platform administrators receive all registered actions through the
        caller's resource/action validation.
        """
        if not self.has_admin_access():
            return set()
        if self.is_platform_admin:
            return set(ActionType)

        if self._model_perms_cache is None:
            cache: set[tuple[str, ActionType]] = set()
            for permission in self.role.permissions if self.role else []:
                model_type = permission.model_type
                if model_type is None:
                    continue
                from app.core.access_catalog import resource_for

                resource = resource_for(model_type.resource_key)
                if resource is not None and permission.action_type in resource.actions:
                    cache.add((resource.key, permission.action_type))
            self._model_perms_cache = cache

        from app.core.access_catalog import resource_for

        resource = resource_for(model_name)
        if resource is None:
            return set()
        return {action for key, action in self._model_perms_cache if key == resource.key}

    def has_perm_for(self, model_name: str, action: ActionType) -> bool:
        from app.core.access_catalog import resource_for

        resource = resource_for(model_name)
        return bool(resource and action in resource.actions and action in self.model_permissions(resource.model_name))

    def has_admin_access(self) -> bool:
        return bool(
            self.is_active and self.role and self.role.codename in (PlatformRoleType.OPERATOR, PlatformRoleType.ADMIN)
        )

    def __str__(self) -> str:
        return self.username
