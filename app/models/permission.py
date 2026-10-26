"""Compatibility aliases; canonical declarations live in domain packages."""

from .identity.permission import Permission
from .managers.permission_manager import PermissionManager

__all__ = ["Permission", "PermissionManager"]
