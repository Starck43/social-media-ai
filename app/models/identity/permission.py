import re
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.types import ActionType

from ...core.config import settings
from ..base import Base, TimestampMixin

if TYPE_CHECKING:
    from .. import ModelType


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("model_type_id", "action_type", name="uq_permission_resource_action"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    action_type: Mapped[ActionType] = ActionType.sa_column(
        type_name="action_type", nullable=False, store_as_name=True  # Хранить как имена (VIEW, CREATE, UPDATE, etc.)
    )
    # Relationships
    model_type_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.DB_SCHEMA}.model_types.id"))
    # eager so `Permission.model_type.model_name` is safe on detached/async users
    # (used by `User.has_perm_for` on the admin and API paths).
    model_type: Mapped["ModelType"] = relationship("ModelType", back_populates="permissions", lazy="selectin")

    def __str__(self) -> str:
        return f"{self.codename}"

    # Manager will be set after class definition to avoid circular imports
    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.permission_manager import PermissionManager

        objects: ClassVar[PermissionManager | BaseManager]
    else:
        objects: ClassVar = None

    @property
    def codename(self) -> str:
        return f"{self.model_type.resource_key}.{self.action_type.db_value}"

    @property
    def app_label(self) -> str:
        return self.model_type.resource_key.split(".")[0]

    @property
    def model_name(self) -> str:
        return self.model_type.model_name

    @property
    def action(self) -> ActionType:
        return self.action_type

    @classmethod
    def action_from_token(cls, token: str) -> ActionType:
        action = ActionType.get_by_value(token)
        if action is None:
            raise ValueError("Unknown permission action")
        return action

    @classmethod
    def validate_codename(cls, codename: str):
        from app.core.access_catalog import resolve_permission_key

        if resolve_permission_key(codename) is None:
            raise ValueError("Unknown permission")

    @classmethod
    def split_codename(cls, codename: str) -> tuple[str, str]:
        from app.core.access_catalog import resolve_permission_key

        cls.validate_codename(codename)
        resource, action = resolve_permission_key(codename)
        return resource.key, action.db_value
