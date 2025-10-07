from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import String, Text, Integer, Table, Column, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base, TimestampMixin
from ..types.models import UserRole

if TYPE_CHECKING:
	from . import User, Permission


# Table for many-to-many relationship between Role and Permission
role_permission = Table(
	"role_permission",
	Base.metadata,
	Column("role_id", Integer, ForeignKey("social_manager.roles.id"), primary_key=True),
	Column("permission_id", Integer, ForeignKey("social_manager.permissions.id"), primary_key=True),
	schema="social_manager"
)


class Role(Base, TimestampMixin):
	__tablename__ = "roles"
	__table_args__ = ({'schema': 'social_manager'},)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
	codename: Mapped[UserRole] = mapped_column(
		Enum(UserRole, name='user_role_type', schema='social_manager', inherit_schema=True)
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
		return f"{self.codename.name}"


# Attach the manager to the Role model
from .managers.role_manager import RoleManager  # noqa: E402
Role.objects = RoleManager(Role)
