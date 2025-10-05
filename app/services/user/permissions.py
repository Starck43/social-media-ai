import logging

from fastapi import HTTPException
from fastapi.params import Depends

from app.models import User, Permission, Role
from app.services.user.auth import get_authenticated_user
from app.types.models import ActionType, UserRole

logger = logging.getLogger(__name__)


def require_permission(permission_codename: ActionType):
	"""Требует конкретное право"""

	async def checker(user: 'User' = Depends(get_authenticated_user)) -> User:
		if not user.has_perm(permission_codename):
			raise HTTPException(
				status_code=403,
				detail=f"Requires permission: {permission_codename}"
			)
		return user

	return checker


def require_any_permission(*permissions: ActionType):
	"""Требует любое из указанных прав"""

	async def checker(user: User = Depends(get_authenticated_user)) -> User:
		if not any(user.has_perm(perm) for perm in permissions):
			raise HTTPException(
				status_code=403,
				detail=f"Requires one of permissions: {permissions}"
			)
		return user

	return checker


class RolePermissionService:
	"""Service for managing role-permission relationships

	Update Strategies:
	— 'replace': Replace all permissions with the new list (default)
	— 'merge': Add new permissions without removing existing ones
	— 'synchronize': Add new permissions and remove those not in the new list
	— 'update_actions': Update actions for the same resources

	Permission patterns support:
	— 'app.*': Matches all permissions for an app
	— '*.action': Matches all permissions with specific action
	— 'app.model.*': Matches all actions for a model
	— '!pattern': Excludes matching permissions
	"""

	@staticmethod
	def expand_permission_patterns(patterns: list[str]) -> list[str]:
		"""
		Expand permission patterns with wildcards into concrete permission codenames.
		Supports exclusion patterns with ! prefix.

		Example:
			['posts.*', '!posts.delete'] → ['posts.view', 'posts.edit', ...]
		"""
		from app.models import Permission

		if not patterns:
			return []

		# Separate include and exclude patterns
		include_patterns = [p for p in patterns if not p.startswith('!')]
		exclude_patterns = [p[1:] for p in patterns if p.startswith('!')]

		# Get all permissions if we have patterns to expand
		all_perms = {p.codename: p for p in Permission.objects.all()}

		# Function to check if a permission matches any pattern
		def matches_any(permission: str, pattern_list: list[str]) -> bool:
			if not pattern_list:
				return False
			import re
			for pattern in pattern_list:
				# Convert pattern to regex (handle * wildcards)
				regex = '^' + pattern.replace('.', '\.').replace('*', '.*') + '$'
				if re.match(regex, permission):
					return True
			return False

		# Start with included permissions
		result = set()

		# Add explicitly listed permissions
		explicit_perms = [p for p in include_patterns if '*' not in p]
		result.update(p for p in explicit_perms if p in all_perms)

		# Add permissions matching include patterns
		pattern_perms = [p for p in include_patterns if '*' in p]
		if pattern_perms:
			for perm in all_perms:
				if matches_any(perm, pattern_perms):
					result.add(perm)

		# Remove excluded permissions
		excluded = set()
		for perm in list(result):
			if matches_any(perm, exclude_patterns):
				result.remove(perm)
				excluded.add(perm)

		return list(result)

	@staticmethod
	def get_role_permissions(role_id: int) -> list[Permission]:
		"""Get all permissions for a role"""
		role = Role.objects.get(id=role_id)
		return role.permissions if role else []

	@staticmethod
	def get_available_permissions() -> list[Permission]:
		"""Get all available permissions"""
		return Permission.objects.order_by(Permission.codename).all()

	@classmethod
	def _get_permission_groups(cls, codenames: list[str]) -> dict[str, dict[str, str]]:
		"""
		Group permissions by 'app.model' and map actions to full codenames.

		Returns:
			dict: A dictionary mapping 'app.model' to a dictionary of actions.
			Example: {'app.model': {'action1': 'app.model.action1'}}
		"""
		groups = {}
		for codename in codenames:
			app_model, action = Permission.split_codename(codename)
			if app_model not in groups:
				groups[app_model] = {}
			groups[app_model][action] = codename
		return groups

	@classmethod
	def _get_updated_permissions(
			cls, current_codenames: list[str], new_codenames: list[str]
	) -> tuple[list[str], list[str]]:
		"""
		Compare current and new permissions to find what needs to be updated.
		Returns: (updated_permissions, added_permissions)
		"""

		current_groups = cls._get_permission_groups(current_codenames)
		new_groups = cls._get_permission_groups(new_codenames)

		updated = []
		added = []

		for app_model, new_actions in new_groups.items():
			if app_model in current_groups:
				# Check each action in new permissions
				for action, codename in new_actions.items():
					if action in current_groups[app_model]:
						# Action exists, check if we need to update
						if current_groups[app_model][action] != codename:
							updated.append(codename)
					else:
						# New action for existing app.model
						added.append(codename)
			else:
				# All actions for this app.model are new
				added.extend(new_actions.values())

		return updated, added

	@classmethod
	def update_role_permissions(
			cls,
			role_codename: str,
			permission_codenames: list[str],
			strategy: str = 'replace'
	) -> dict[str, list[str]]:
		"""
		Update permissions for a role using the specified strategy.

		Args:
			role_codename: The codename of the role to update (case-insensitive)
			permission_codenames: List of permission patterns (supports wildcards and exclusions)
			strategy: Update strategy — 'replace', 'merge', 'synchronize', or 'update_actions'

		Returns:
			dict: {
				'added': list of added permission codenames,
				'removed': list of removed permission codenames,
				'updated': list of updated permission codenames,
				'unchanged': list of unchanged permission codenames
			}
		"""
		# Expand permission patterns to concrete codenames
		expanded_codenames = cls.expand_permission_patterns(permission_codenames)

		logger.debug(f"Expanded permissions for {role_codename}: {expanded_codenames}")

		role = Role.objects.get(name=role_codename.lower())
		if not role:
			raise ValueError(f"Role '{role_codename}' not found")

		current_permissions = set(p.codename for p in role.permissions)
		new_permissions = set(expanded_codenames)

		if strategy == 'replace':
			return cls._update_replace(role, current_permissions, new_permissions)
		elif strategy == 'merge':
			return cls._update_merge(role, current_permissions, new_permissions)
		elif strategy == 'synchronize':
			return cls._update_synchronize(role, current_permissions, new_permissions)
		elif strategy == 'update_actions':
			return cls._update_actions(role, current_permissions, new_permissions)
		else:
			raise ValueError(f"Unknown update strategy: {strategy}")

	@classmethod
	def _update_replace(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
		"""Replace all permissions with the new list."""
		if current == new:
			return {
				'added': [],
				'removed': [],
				'updated': [],
				'unchanged': list(current)
			}

		removed = list(current - new)
		added = list(new - current)

		role.permissions = cls.get_permissions_by_codenames(list(new))
		role.save()

		return {
			'added': added,
			'removed': removed,
			'updated': [],
			'unchanged': list(current & new)
		}

	@classmethod
	def _update_merge(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
		"""Add new permissions without removing existing ones."""
		to_add = new - current
		if not to_add:
			return {
				'added': [],
				'removed': [],
				'updated': [],
				'unchanged': list(current)
			}

		added_perms = cls.get_permissions_by_codenames(list(to_add))
		role.permissions.extend(added_perms)
		role.save()

		return {
			'added': list(to_add),
			'removed': [],
			'updated': [],
			'unchanged': list(current)
		}

	@classmethod
	def _update_synchronize(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
		"""Add new permissions and remove those not in the new list."""
		to_add = new - current
		to_remove = current - new

		if not to_add and not to_remove:
			return {
				'added': [],
				'removed': [],
				'updated': [],
				'unchanged': list(current)
			}

		# Get current permissions as a dictionary for easy removal
		current_perms = {p.codename: p for p in role.permissions}

		# Remove permissions
		for codename in to_remove:
			if codename in current_perms:
				role.permissions.remove(current_perms[codename])

		# Add new permissions
		added_perms = cls.get_permissions_by_codenames(list(to_add))
		role.permissions.extend(added_perms)
		role.save()

		return {
			'added': list(to_add),
			'removed': list(to_remove),
			'updated': [],
			'unchanged': list(current & new)
		}

	@classmethod
	def _update_actions(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
		"""Update actions for the same resources."""
		current_groups = cls._get_permission_groups(list(current))
		new_groups = cls._get_permission_groups(list(new))

		updated = []
		added = []
		removed = []

		# Handle updates and additions
		for app_model, new_actions in new_groups.items():
			if app_model in current_groups:
				# Check for updates to existing permissions
				for action, codename in new_actions.items():
					if action in current_groups[app_model]:
						# Action exists, check if we need to update
						if current_groups[app_model][action] != codename:
							updated.append(codename)
					else:
						# New action for existing app.model
						added.append(codename)
			else:
				# All actions for this app.model are new
				added.extend(new_actions.values())

		# Handle removals (permissions that exist in current but not in new for the same app_model)
		for app_model, current_actions in current_groups.items():
			if app_model in new_groups:
				for action, codename in current_actions.items():
					if action not in new_groups[app_model]:
						removed.append(codename)

		if not (updated or added or removed):
			return {
				'added': [],
				'removed': [],
				'updated': [],
				'unchanged': list(current)
			}

		# Apply changes
		current_perms = {p.codename: p for p in role.permissions}

		# Remove old permissions
		for codename in removed:
			if codename in current_perms:
				role.permissions.remove(current_perms[codename])

		# Add new permissions
		new_perms = cls.get_permissions_by_codenames(added + updated)
		role.permissions.extend(new_perms)
		role.save()

		return {
			'added': added,
			'removed': removed,
			'updated': updated,
			'unchanged': list(current - set(removed) - set(updated))
		}

	@staticmethod
	def get_permissions_by_codenames(codenames: list[str]) -> list[Permission]:
		"""Get permissions by their codenames"""
		return Permission.objects.filter(Permission.codename.in_(codenames)).all()

	@staticmethod
	def assign_default_permissions():
		"""Assign default permissions based on role hierarchy"""
		default_permissions = {
			UserRole.VIEWER: [
				"social.notification.view",
				"social.post.view",
			],
			UserRole.AI_BOT: [
				"social.notification.view",
				"social.post.view",
				"social.post.create",
			],
			UserRole.MANAGER: [
				"social.notification.view",
				"social.post.*",
				"dashboard.statistics.view",
			],
			UserRole.ANALYST: [
				"social.notification.view",
				"social.post.view",
				"dashboard.aianalysisresult.*",
			],
			UserRole.MODERATOR: [
				"social.notification.*",
				"social.post.*",
				"dashboard.aianalysisresult.view",
			],
			UserRole.ADMIN: [
				"*"
			],
		}

		for role_enum, permission_codenames in default_permissions.items():
			# Get the role by enum value
			role: Role = Role.objects.get(codename=role_enum.name)
			if not role:
				continue

			if role.permissions:
				print(f"Role {role_enum.name}: already has permissions. Skipping...")
				continue

			print(f"\nRole {role_enum.name}:")

			if permission_codenames == ["*"]:
				# Superuser gets all permissions
				role_permissions = Permission.objects.all()
				print(f"✅ Assigned all permissions as default\n")
				role.permissions = role_permissions

			else:
				# Get all available permissions for a wildcard matching
				all_permissions = Permission.objects.all()
				permissions = []

				for codename_pattern in permission_codenames:
					if codename_pattern.endswith('.*'):
						# Handle wildcard pattern
						app_prefix = codename_pattern[:-2]  # Remove '.*' from the end
						# Find all permissions that start with the app_prefix
						app_permissions = [
							p for p in all_permissions
							if p.codename.startswith(app_prefix)
						]
						permissions.extend(app_permissions)
						print(f"🔍 Found {len(app_permissions)} permissions matching pattern: {codename_pattern}")
					else:
						# Exact match using PermissionManager
						permission = Permission.objects.get_by_codename(codename_pattern)
						if permission:
							permissions.append(permission)
						else:
							print(f"⚠️ Permission not found: {codename_pattern}")

				# Remove duplicates and assign to role
				unique_permissions = list({p.id: p for p in permissions}.values())
				role.permissions = unique_permissions
				print(f"✅ Assigned {len(unique_permissions)} permissions")
				print(f"   {list(p.codename for p in unique_permissions)}\n")

			# Save the role with updated permissions
			role.save()
