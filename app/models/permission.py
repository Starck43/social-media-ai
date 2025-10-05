import re
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Integer, String, UniqueConstraint, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.types.models import ActionType
from .base import Base

if TYPE_CHECKING:
	from . import ModelType


class Permission(Base):
	__tablename__ = "permissions"
	__table_args__ = (
		UniqueConstraint('codename', 'model_type_id', name='uq_permission_model_type'),
		CheckConstraint("codename ~ '^[a-z_]+\\.[a-z_]+\\.[a-z]+$'", name="check_codename_format"),
		{'schema': 'social_manager'}
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	codename: Mapped[str] = mapped_column(String(100), nullable=False)
	name: Mapped[str] = mapped_column(String(200), nullable=False)
	action_type: Mapped[ActionType]

	# Relationships
	model_type_id: Mapped[int] = mapped_column(ForeignKey("social_manager.model_types.id"))
	model_type: Mapped["ModelType"] = relationship("ModelType", back_populates="permissions")

	def __str__(self) -> str:
		return f"{self.codename}"

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.permission_manager import PermissionManager
		objects: ClassVar[PermissionManager | BaseManager]
	else:
		objects: ClassVar = None

	@property
	def app_label(self) -> str:
		return self.codename.split('.')[0]

	@property
	def model_name(self) -> str:
		return self.codename.split('.')[1]

	@property
	def action(self) -> ActionType:
		return ActionType(self.codename.split('.')[2])

	@staticmethod
	def validate_codename(codename: str):
		pattern = r'^[a-z_]+\.[a-z_]+\.[a-z]+$'
		if not re.match(pattern, codename):
			raise ValueError(f"Invalid codename format: {codename}")

		action_part = codename.split('.')[2]
		try:
			# action_part must be a value of ActionType enum in lowercase
			ActionType(action_part)
		except ValueError:
			raise ValueError(f"Invalid action type in codename: {action_part}")

	@classmethod
	def split_codename(cls, codename: str) -> tuple[str, str]:
		"""Split permission codename into (app_model, action) parts."""
		cls.validate_codename(codename)

		parts = codename.split('.')
		if len(parts) < 3:  # Should be at least 'app.model.action'
			return codename, ''
		return '.'.join(parts[:2]), parts[2]


# Attach the manager to the Permission model
from .managers.permission_manager import PermissionManager  # noqa: E402
Permission.objects = PermissionManager()
