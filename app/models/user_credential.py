"""Compatibility aliases; canonical declarations live in domain packages."""

from .identity.user_credential import UserCredential
from .managers.user_credential_manager import UserCredentialManager

__all__ = ["UserCredential", "UserCredentialManager"]
