from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import String, Text, Integer, Table, Column, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base, TimestampMixin
from ..core.config import settings
from ..types.models import UserRoleType

if TYPE_CHECKING:
	from . import User, Permission


# Table for many-to-many relationship between Role and Permission
role_permission = Table(
	"role_permission",
	Base.metadata,
	Column("role_id", Integer, ForeignKey("social_manager.roles.id"), primary_key=True),
	Column("permission_id", Integer, ForeignKey("social_manager.permissions.id"), primary_key=True),
	schema=settings.DB_SCHEMA
)


class Role(Base, TimestampMixin):
	__tablename__ = "roles"
	__table_args__ = {'schema': settings.DB_SCHEMA}

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
	codename: Mapped[UserRoleType] = UserRoleType.sa_column(
		type_name='user_role_type',
		default=UserRoleType.VIEWER,
		store_as_name=True  # Хранить как имена (VIEWER, AI_BOT, etc.)
	)
	description: Mapped[str] = mapped_column(Text, nullable=True)

	# Relation one-to-many with User
	users: Mapped[list["User"]] = relationship("User", back_populates="role")
	# Relation many-to-many with Permission
	permissions: Mapped[list["Permission"]] = relationship(
		"Permission",
		secondary=role_permission,
		backref="roles"
	)

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.role_manager import RoleManager
		from .managers.base_manager import BaseManager
		objects: ClassVar[RoleManager | BaseManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		return f"{self.codename}"


# Attach the manager to the Role model
from .managers.role_manager import RoleManager  # noqa: E402
Role.objects = RoleManager(Role)
