"""Compatibility aliases; canonical declarations live in domain packages."""

from .identity.role import PlatformRole, Role, role_permission
from .managers.role_manager import RoleManager

__all__ = ["PlatformRole", "Role", "RoleManager", "role_permission"]
