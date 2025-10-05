"""
Role Manager for SQLAlchemy models.
This module provides a RoleManager class for the Role model.
"""
from typing import Type, TYPE_CHECKING, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..role import Role
	from ..permission import Permission


class RoleManager(BaseManager):
	"""Manager for Role model with a custom query and CRUD methods."""

	def __init__(self, model_cls: Type['Role']):
		super().__init__(model_cls)

	def _add_permission_with_session(self, db: Session, role: 'Role', permission: 'Permission') -> 'Role':
		"""Internal method to add a permission to a role with an existing session."""
		try:
			if permission not in role.permissions:
				role.permissions.append(permission)
				db.add(role)
				db.commit()
				db.refresh(role)
			return role
		except SQLAlchemyError as e:
			db.rollback()
			raise e

	def _remove_permission_with_session(self, db: Session, role: 'Role', permission: 'Permission') -> 'Role':
		"""Internal method to remove a permission from a role with an existing session."""
		try:
			if permission in role.permissions:
				role.permissions.remove(permission)
				db.add(role)
				db.commit()
				db.refresh(role)
			return role
		except SQLAlchemyError as e:
			db.rollback()
			raise e

	def get_with_permissions(self, role_name: str, db: Session = None) -> Optional['Role']:
		"""
		Get a role by name with its permissions loaded.

		Args:
			role_name: Name of the role to retrieve
			db: Optional SQLAlchemy session. If not provided, will be handled by get_queryset()

		Returns:
			Role instance with loaded permissions or None if not found
		"""
		return (
			self.get_queryset(db)
				.options(joinedload(self.model.permissions))
				.filter(self.model.name == role_name.lower())
				.first()
		)

	def add_permission(self, role: 'Role', permission: 'Permission', db: Session = None) -> 'Role':
		"""Add a permission to a role.

		Args:
			role: The role to modify
			permission: The permission to add
			db: Optional SQLAlchemy session

		Returns:
			The updated Role instance
		"""
		if db is None:
			from app.core.database import db_session
			with db_session() as session:
				return self._add_permission_with_session(session, role, permission)
		return self._add_permission_with_session(db, role, permission)

	def remove_permission(self, role: 'Role', permission: 'Permission', db: Session = None) -> 'Role':
		"""Remove a permission from a role.

		Args:
			role: The role to modify
			permission: The permission to remove
			db: Optional SQLAlchemy session

		Returns:
			The updated Role instance
		"""
		if db is None:
			from app.core.database import db_session
			with db_session() as session:
				return self._remove_permission_with_session(session, role, permission)
		return self._remove_permission_with_session(db, role, permission)
