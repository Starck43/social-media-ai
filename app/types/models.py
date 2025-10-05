from enum import Enum


class UserRole(Enum):
	"""User roles in hierarchy order — DO NOT CHANGE THE ORDER!"""
	VIEWER = "Basic read-only access to view content"
	AI_BOT = "AI assistant with limited access to specific features"
	MANAGER = "Can manage team members and basic content"
	ANALYST = "Can view and analyze data and reports"
	MODERATOR = "Can moderate content and manage users"
	ADMIN = "Administrator with almost full access"
	SUPERUSER = "Superuser with full system access"

	def __str__(self) -> str:
		return self.name.lower()


class ActionType(Enum):
	VIEW = "view"
	CREATE = "create"
	UPDATE = "update"
	DELETE = "delete"
	ANALYZE = "analyze"
	MODERATE = "moderate"
	EXPORT = "export"
	CONFIGURE = "configure"

	def __str__(self) -> str:
		return self.value
