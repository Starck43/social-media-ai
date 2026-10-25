"""Compatibility exports; role managers bind centrally in app.models."""

from .identity.role import PlatformRole, Role, role_permission
from .managers.role_manager import RoleManager
