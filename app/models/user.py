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
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{settings.DB_SCHEMA}.roles.id"), nullable=False)
    role: Mapped["Role"] = relationship("Role", back_populates="users")

    # Manager will be set after class definition to avoid circular imports
    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager
        from .managers.user_manager import UserManager
        objects: ClassVar[UserManager | BaseManager]
    else:
        objects: ClassVar = None

    # Class-level default so the cache exists even on ORM-loaded instances
    # (SQLAlchemy populates via __new__, skipping __init__).
    _model_perms_cache: set[tuple[str, ActionType]] | None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model_perms_cache = None

    def _is_superuser_role(self) -> bool:
        """True for the user's own `is_superuser` flag or the SUPERUSER role.

        `Role.codename` comes back from the DB as its string value ("SUPERUSER")
        rather than the enum member, so both shapes are normalised before the
        lookup.
        """
        if self.is_superuser:
            return True
        codename = self.role.codename if self.role else None
        if codename is None:
            return False
        name = codename.name if hasattr(codename, "name") else str(codename)
        return name == UserRoleType.SUPERUSER.name

    def model_permissions(self, model_name: str) -> set[ActionType]:
        """Every `ActionType` the user's role grants on one model.

        Same source as `has_perm_for` — the structured columns
        (`model_types.model_name` + `action_type`) rather than the codename,
        for the reason documented there. Superusers (and the SUPERUSER role)
        get the whole enum.

        Callers that need more than a yes/no (the admin backend turns the role's
        rights into the per-model grants of the console) read the set once
        instead of asking the same question seven times.
        """
        if self._is_superuser_role():
            return set(ActionType)

        if self._model_perms_cache is None:
            cache: set[tuple[str, ActionType]] = set()
            for permission in (self.role.permissions if self.role else []):
                model_type = permission.model_type
                if model_type is None:
                    continue
                cache.add((model_type.model_name.lower(), permission.action_type))
            self._model_perms_cache = cache

        wanted = model_name.lower()
        return {action for name, action in self._model_perms_cache if name == wanted}

    def has_perm_for(self, model_name: str, action: ActionType) -> bool:
        """Check a permission by model + action instead of by codename.

        The stored codenames are not reliably parseable:
        ``generate_permission_codename`` interpolated ``action.value``, which for
        these enums is the display tuple, so rows like
        ``social.AgentTask.('view', 'Просмотр', '👀')`` exist in ``permissions``.
        The structured columns — ``permissions.model_type_id`` and
        ``action_type`` — are correct, so the check goes through
        ``model_types.model_name`` plus the action enum.

        Superusers (and the SUPERUSER role) always pass.
        """
        if self._is_superuser_role():
            return True

        return action in self.model_permissions(model_name)

    def has_admin_access(self) -> bool:
        """Whether the user may enter the operator console (/admin).

        Superusers always may. Anyone else may only if their role carries at
        least one model `view` permission — they then see only the models that
        permission grants. Requires the ``role`` relationship (with its
        ``permissions``) to be eager-loaded.
        """
        if self._is_superuser_role():
            return True
        for permission in self.role.permissions:
            model_type = permission.model_type
            if model_type is not None and permission.action_type == ActionType.VIEW:
                return True
        return False

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
