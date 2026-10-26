"""Compatibility aliases; canonical declarations live in domain packages."""

from .identity.user import User
from .managers.user_manager import UserManager

__all__ = ["User", "UserManager"]
