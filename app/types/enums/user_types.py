"""User-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


@database_enum
class UserRoleType(Enum):
	"""User roles in hierarchy order — DO NOT CHANGE THE ORDER!"""

	VIEWER = "Basic read-only access to view content"
	AI_BOT = "AI assistant with limited access to specific features"
	MANAGER = "Can manage team members and basic content"
	ANALYST = "Can view and analyze data and reports"
	MODERATOR = "Can moderate content and manage users"
	ADMIN = "Administrator with almost full access"
	SUPERUSER = "Superuser with full system access"


@database_enum
class ActionType(Enum):
	"""User permission action types."""

	VIEW = "view"
	CREATE = "create"
	UPDATE = "update"
	DELETE = "delete"
	ANALYZE = "analyze"
	MODERATE = "moderate"
	EXPORT = "export"
	CONFIGURE = "configure"
