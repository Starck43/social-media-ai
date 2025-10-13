from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..permission import Permission
	from app.types.models import ActionType


class PermissionManager(BaseManager):
	"""
	Manager for Permission model with codename-based operations.
	
	Provides methods for:
	— Permission CRUD operations
	— Finding permissions by a codename, action type, and model
	— Permission validation and creation
	— Model-based permission queries
	"""
	
	def __init__(self):
		from ..permission import Permission
		super().__init__(Permission)
	
	async def get_by_codename(self, codename: str) -> Optional['Permission']:
		"""
		Get permission by a codename.

		Args:
			codename: Permission codename (app.model.action format)

		Returns:
			Permission object or None
		"""
		return await self.filter(codename=codename).first()
	
	async def get_by_action_type(
		self,
		action_type: 'ActionType'
	) -> list['Permission']:
		"""
		Get all permissions with specific action type.

		Args:
			action_type: Action type (VIEW, CREATE, UPDATE, DELETE, EXECUTE)

		Returns:
			List of Permission objects
		"""
		return await self.filter(action_type=action_type)
	
	async def get_for_model(
		self,
		app_name: str,
		model_name: str,
		action_type: Optional['ActionType'] = None
	) -> list['Permission']:
		"""
		Get all permissions for a specific model.

		Args:
			app_name: Application name
			model_name: Model name
			action_type: An optional filter by action type

		Returns:
			List of Permission objects
		"""

		# Get permissions where codename starts with 'app.model'
		prefix = f"{app_name}.{model_name}."
		all_perms = await self.filter()
		
		perms = [p for p in all_perms if p.codename.startswith(prefix)]
		
		if action_type:
			perms = [p for p in perms if p.action_type == action_type]
		
		return perms
	
	async def get_for_model_type(
		self,
		model_type_id: int,
		action_type: Optional['ActionType'] = None
	) -> list['Permission']:
		"""
		Get all permissions for a specific model type.

		Args:
			model_type_id: ModelType ID
			action_type: An optional filter by action type

		Returns:
			List of Permission objects
		"""
		qs = self.filter(model_type_id=model_type_id)
		
		if action_type:
			qs = qs.filter(action_type=action_type)
		
		return await qs
	
	async def create_permission(
		self,
		codename: str,
		name: str,
		action_type: 'ActionType',
		model_type_id: int
	) -> 'Permission':
		"""
		Create a new permission with validation.

		Args:
			codename: Permission codename (app.model.action format)
			name: Human-readable permission name
			action_type: Action type
			model_type_id: ModelType ID

		Returns:
			Created Permission object

		Raises:
			ValueError: If codename format is invalid or permission exists
		"""

		# Validate codename format
		self.model.validate_codename(codename)
		
		# Check if already exists
		existing = await self.get_by_codename(codename)
		if existing:
			raise ValueError(f"Permission with codename '{codename}' already exists")
		
		return await self.create(
			codename=codename,
			name=name,
			action_type=action_type,
			model_type_id=model_type_id
		)
	
	async def bulk_create_for_model(
		self,
		app_name: str,
		model_name: str,
		model_type_id: int,
		action_types: list['ActionType']
	) -> list['Permission']:
		"""
		Create permissions for all specified actions on a model.

		Args:
			app_name: Application name
			model_name: Model name
			model_type_id: ModelType ID
			action_types: List of action types to create

		Returns:
			List of created Permission objects
		"""
		created = []
		
		for action_type in action_types:
			codename = f"{app_name}.{model_name}.{action_type.value}"
			name = f"Can {action_type.value} {model_name}"
			
			# Check if exists
			existing = await self.get_by_codename(codename)
			if existing:
				created.append(existing)
				continue
			
			# Create new
			perm = await self.create(
				codename=codename,
				name=name,
				action_type=action_type,
				model_type_id=model_type_id
			)
			created.append(perm)
		
		return created
	
	async def get_with_model_type(
		self,
		permission_id: int
	) -> Optional['Permission']:
		"""
		Get permission with prefetched model_type relationship.

		Args:
			permission_id: Permission ID

		Returns:
			Permission object with model_type loaded
		"""
		return await (
			self.filter(id=permission_id)
			.select_related("model_type")
			.first()
		)
	
	async def search_permissions(
		self,
		query: str,
		action_type: Optional['ActionType'] = None
	) -> list['Permission']:
		"""
		Search permissions by a codename or name.

		Args:
			query: Search query
			action_type: An optional filter by action type

		Returns:
			List of matching Permission objects
		"""
		all_perms = await self.filter()
		
		# Filter by text search
		perms = [
			p for p in all_perms
			if query.lower() in p.codename.lower()
			or query.lower() in p.name.lower()
		]
		
		# Filter by action type
		if action_type:
			perms = [p for p in perms if p.action_type == action_type]
		
		return perms
	
	async def get_stats(self) -> dict:
		"""
		Get statistics about permissions.

		Returns:
			Dict with permission statistics
		"""
		all_perms = await self.filter()
		
		stats = {
			'total': len(all_perms),
			'by_action': {},
			'by_app': {}
		}
		
		for perm in all_perms:
			# Count by action type
			action = perm.action_type.value if hasattr(perm.action_type, 'value') else str(perm.action_type)
			stats['by_action'][action] = stats['by_action'].get(action, 0) + 1
			
			# Count by app
			app = perm.app_label
			stats['by_app'][app] = stats['by_app'].get(app, 0) + 1
		
		return stats
