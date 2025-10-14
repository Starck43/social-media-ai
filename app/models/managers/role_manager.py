from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..role import Role
	from ..permission import Permission
	from app.types import UserRoleType, ActionType


class RoleManager(BaseManager['Role']):
	"""
	Manager for Role model with permission management.
	
	Provides methods for:
	— Role CRUD operations
	— Permission assignment/removal
	— Role-based access control queries
	— Finding roles by a codename and permissions
	"""

	def __init__(self, model_cls):
		super().__init__(model_cls)

	async def get_by_name(self, name: str) -> Optional['Role']:
		"""
		Get role by name.

		Args:
			name: Role name

		Returns:
			Role object or None
		"""
		return await self.filter(name=name.lower()).first()

	async def get_by_codename(self, codename: 'UserRoleType') -> Optional['Role']:
		"""
		Get role by a codename.

		Args:
			codename: Role codename (UserRole enum)

		Returns:
			Role object or None
		"""
		return await self.filter(codename=codename).first()

	async def get_with_permissions(self, role_id: int) -> Optional['Role']:
		"""
		Get role with prefetched permissions.

		Args:
			role_id: Role ID

		Returns:
			Role object with permissions loaded
		"""
		return await (
			self.filter(id=role_id)
			.prefetch_related("permissions")
			.first()
		)

	async def get_with_users(self, role_id: int) -> Optional['Role']:
		"""
		Get role with prefetched users.

		Args:
			role_id: Role ID

		Returns:
			Role object with users loaded
		"""
		return await (
			self.filter(id=role_id)
			.prefetch_related("users")
			.first()
		)

	async def create_role(
		self,
		name: str,
		codename: 'UserRoleType',
		description: Optional[str] = None
	) -> 'Role':
		"""
		Create a new role.

		Args:
			name: Role name
			codename: Role codename (UserRole enum)
			description: Optional description

		Returns:
			Created Role object

		Raises:
			ValueError: If role with same name already exists
		"""
		existing = await self.get_by_name(name)
		if existing:
			raise ValueError(f"Role with name '{name}' already exists")

		return await self.create(
			name=name.lower(),
			codename=codename,
			description=description
		)

	async def add_permission(
		self,
		role_id: int,
		permission_id: int
	) -> Optional['Role']:
		"""
		Add a permission to a role.

		Args:
			role_id: Role ID
			permission_id: Permission ID

		Returns:
			Updated Role object or None if role not found
		"""
		from ..permission import Permission
		
		role = await self.get_with_permissions(role_id)
		if not role:
			return None
		
		permission = await Permission.objects.get(id=permission_id)
		if not permission:
			return None
		
		# Add permission if not already present
		if permission not in role.permissions:
			role.permissions.append(permission)
			# Session will auto-commit through relationship
		
		return role

	async def remove_permission(
		self,
		role_id: int,
		permission_id: int
	) -> Optional['Role']:
		"""
		Remove a permission from a role.

		Args:
			role_id: Role ID
			permission_id: Permission ID

		Returns:
			Updated Role object or None if role not found
		"""
		from ..permission import Permission
		
		role = await self.get_with_permissions(role_id)
		if not role:
			return None
		
		permission = await Permission.objects.get(id=permission_id)
		if not permission:
			return None
		
		# Remove permission if present
		if permission in role.permissions:
			role.permissions.remove(permission)
		
		return role

	async def get_roles_with_permission(
		self,
		permission_codename: str
	) -> list['Role']:
		"""
		Get all roles that have a specific permission.

		Args:
			permission_codename: Permission codename

		Returns:
			List of Role objects
		"""
		from ..permission import Permission
		
		permission = await Permission.objects.filter(codename=permission_codename).first()
		if not permission:
			return []
		
		# Get all roles with prefetched permissions
		all_roles = await self.filter().prefetch_related("permissions")
		
		return [
			role for role in all_roles
			if permission in role.permissions
		]

	async def has_permission(
		self,
		role_id: int,
		permission_codename: str
	) -> bool:
		"""
		Check if a role has a specific permission.

		Args:
			role_id: Role ID
			permission_codename: Permission codename

		Returns:
			True if role has the permission
		"""
		role = await self.get_with_permissions(role_id)
		if not role:
			return False
		
		return any(
			perm.codename == permission_codename
			for perm in role.permissions
		)

	async def get_permissions_for_role(
		self,
		role_id: int,
		action_type: Optional['ActionType'] = None
	) -> list['Permission']:
		"""
		Get all permissions for a role.

		Args:
			role_id: Role ID
			action_type: An optional filter by action type

		Returns:
			List of Permission objects
		"""
		role = await self.get_with_permissions(role_id)
		if not role:
			return []
		
		permissions = role.permissions
		
		if action_type:
			permissions = [
				perm for perm in permissions
				if perm.action_type == action_type
			]
		
		return permissions

	async def get_stats(self) -> dict:
		"""
		Get statistics about roles.

		Returns:
			Dict with role statistics
		"""
		all_roles = await self.filter()
		
		stats = {
			'total': len(all_roles),
			'by_codename': {}
		}
		
		for role in all_roles:
			codename = role.codename.value if hasattr(role.codename, 'value') else str(role.codename)
			stats['by_codename'][codename] = {
				'name': role.name,
				'description': role.description
			}
		
		return stats
