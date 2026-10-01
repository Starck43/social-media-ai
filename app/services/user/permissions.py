import logging

from app.models import Permission, Role
from app.types import UserRoleType

logger = logging.getLogger(__name__)


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
	async def _set_role_permissions(role_id: int, permission_ids: list[int]) -> None:
		"""Replace a role's permission links with exactly `permission_ids`.

		`role_permission` is an association table without a model, so the write
		goes through `RoleManager.set_permissions` — the single place that knows
		how to rewrite it. The role row is read through the async managers (its
		session is closed by then), so mutating the ORM collection and calling
		`role.save()` — the legacy sync path — no longer applies.
		"""
		from app.models.managers.role_manager import RoleManager

		await RoleManager(Role).set_permissions(role_id, permission_ids)

	@staticmethod
	async def expand_permission_patterns(patterns: list[str]) -> list[str]:
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
		all_perms = {p.codename: p for p in await Permission.objects.all()}
		# Patterns are written in the readable `social.Source.view` form while
		# the stored codenames are lowercase (`Permission.check_codename_format`),
		# and rows created before that constraint kept the mixed-case form. Match
		# on the lowercased side so one matrix drives every database.
		by_lowercase = {codename.lower(): codename for codename in all_perms}

		# Function to check if a permission matches any pattern
		def matches_any(permission: str, pattern_list: list[str]) -> bool:
			if not pattern_list:
				return False
			import re
			for pattern in pattern_list:
				# Convert pattern to regex (handle * wildcards)
				regex = '^' + pattern.replace('.', r'\.').replace('*', '.*') + '$'
				if re.match(regex, permission, re.IGNORECASE):
					return True
			return False

		# Start with included permissions
		result = set()

		# Add explicitly listed permissions
		explicit_perms = [p for p in include_patterns if '*' not in p]
		result.update(by_lowercase[p.lower()] for p in explicit_perms if p.lower() in by_lowercase)

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
	async def get_role_permissions(role_id: int) -> list[Permission]:
		"""Get all permissions for a role"""
		role = await Role.objects.filter(id=role_id).prefetch_related("permissions").first()
		return list(role.permissions) if role else []

	@staticmethod
	async def get_available_permissions() -> list[Permission]:
		"""Get all available permissions"""
		return list(await Permission.objects.order_by(Permission.codename).all())

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
	async def update_role_permissions(
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
		expanded_codenames = await cls.expand_permission_patterns(permission_codenames)

		logger.debug(f"Expanded permissions for {role_codename}: {expanded_codenames}")

		role = await Role.objects.filter(name=role_codename.lower()).prefetch_related("permissions").first()
		if not role:
			raise ValueError(f"Role '{role_codename}' not found")

		current_permissions = set(p.codename for p in role.permissions)
		new_permissions = set(expanded_codenames)

		if strategy == 'replace':
			return await cls._update_replace(role, current_permissions, new_permissions)
		elif strategy == 'merge':
			return await cls._update_merge(role, current_permissions, new_permissions)
		elif strategy == 'synchronize':
			return await cls._update_synchronize(role, current_permissions, new_permissions)
		elif strategy == 'update_actions':
			return await cls._update_actions(role, current_permissions, new_permissions)
		else:
			raise ValueError(f"Unknown update strategy: {strategy}")

	@classmethod
	async def _update_replace(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
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

		permissions = await cls.get_permissions_by_codenames(list(new))
		await cls._set_role_permissions(role.id, [p.id for p in permissions])

		return {
			'added': added,
			'removed': removed,
			'updated': [],
			'unchanged': list(current & new)
		}

	@classmethod
	async def _update_merge(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
		"""Add new permissions without removing existing ones."""
		to_add = new - current
		if not to_add:
			return {
				'added': [],
				'removed': [],
				'updated': [],
				'unchanged': list(current)
			}

		# The role row is detached here, so the ORM collection cannot be mutated
		# and `role.save()` (legacy sync session) does not apply — write the
		# association table directly, like the other strategies.
		kept = await cls.get_permissions_by_codenames(list(current))
		added_perms = await cls.get_permissions_by_codenames(list(to_add))
		await cls._set_role_permissions(role.id, [p.id for p in kept + added_perms])

		return {
			'added': list(to_add),
			'removed': [],
			'updated': [],
			'unchanged': list(current)
		}

	@classmethod
	async def _update_synchronize(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
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

		added_perms = await cls.get_permissions_by_codenames(list(to_add))
		kept = await cls.get_permissions_by_codenames(list(current & new))
		await cls._set_role_permissions(role.id, [p.id for p in kept + added_perms])

		return {
			'added': list(to_add),
			'removed': list(to_remove),
			'updated': [],
			'unchanged': list(current & new)
		}

	@classmethod
	async def _update_actions(cls, role: 'Role', current: set[str], new: set[str]) -> dict[str, list[str]]:
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

		new_perms = await cls.get_permissions_by_codenames(added + updated)
		kept = await cls.get_permissions_by_codenames(list(current - set(removed) - set(updated)))
		await cls._set_role_permissions(role.id, [p.id for p in kept + new_perms])

		return {
			'added': added,
			'removed': removed,
			'updated': updated,
			'unchanged': list(current - set(removed) - set(updated))
		}

	@staticmethod
	async def get_permissions_by_codenames(codenames: list[str]) -> list[Permission]:
		"""Get permissions by their codenames"""
		if not codenames:
			return []
		return list(await Permission.objects.filter(Permission.codename.in_(codenames)).all())

	@staticmethod
	async def assign_default_permissions():
		"""Assign default permissions based on role hierarchy"""
		default_permissions = {
			UserRoleType.VIEWER: [
				"social.notification.view",
				"social.post.view",
			],
			UserRoleType.AI_BOT: [
				"social.notification.view",
				"social.post.view",
				"social.post.create",
			],
			UserRoleType.MANAGER: [
				"social.notification.view",
				"social.post.*",
				"dashboard.statistics.view",
			],
			UserRoleType.ANALYST: [
				"social.notification.view",
				"social.post.view",
				"dashboard.aianalysisresult.*",
			],
			UserRoleType.MODERATOR: [
				"social.notification.*",
				"social.post.*",
				"dashboard.aianalysisresult.view",
			],
			UserRoleType.ADMIN: [
				"*"
			],
		}

		for role_enum, permission_codenames in default_permissions.items():
			# Get the role by enum value
			role: Role | None = await Role.objects.filter(codename=role_enum.name).prefetch_related("permissions").first()
			if not role:
				continue

			if role.permissions:
				print(f"Role {role_enum.name}: already has permissions. Skipping...")
				continue

			print(f"\nRole {role_enum.name}:")

			if permission_codenames == ["*"]:
				# Superuser gets all permissions
				role_permissions = await Permission.objects.all()
				print(f"✅ Assigned all permissions as default\n")
				await RolePermissionService._set_role_permissions(role.id, [p.id for p in role_permissions])

			else:
				# Get all available permissions for a wildcard matching
				all_permissions = await Permission.objects.all()
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
						# Exact match. Queried through `filter` rather than the
						# PermissionManager helper: `Model.objects` is typed as a
						# `Manager | BaseManager` union across the models, which
						# hides the manager-specific methods from type checkers.
						permission = await Permission.objects.filter(Permission.codename == codename_pattern).first()
						if permission:
							permissions.append(permission)
						else:
							print(f"⚠️ Permission not found: {codename_pattern}")

				# Remove duplicates and assign to role
				unique_permissions = list({p.id: p for p in permissions}.values())
				await RolePermissionService._set_role_permissions(role.id, [p.id for p in unique_permissions])
				print(f"✅ Assigned {len(unique_permissions)} permissions")
				print(f"   {list(p.codename for p in unique_permissions)}\n")
