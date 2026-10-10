"""Identity-domain exports preserving root registration and legacy bindings.

The role-permission association has one canonical declaration in role.py.
No new manager, app label, permission policy or vault tenancy is introduced.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_type import ModelType
    from .permission import Permission
    from .role import Role, role_permission
    from .user import User
    from .user_credential import UserCredential

__all__ = ["User", "Role", "Permission", "ModelType", "UserCredential", "role_permission"]
_MODULES = {
    "User": "user",
    "Role": "role",
    "Permission": "permission",
    "ModelType": "model_type",
    "UserCredential": "user_credential",
    "role_permission": "role",
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
