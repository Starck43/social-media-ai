"""Basic credential bundles for the existing workspace connection secret vault.

The service is responsible for authorization and binding the encrypted value to
one WorkspaceConnectionSecret; this module never persists or logs credentials.
"""

import json
from dataclasses import dataclass

from app.utils.crypto import decrypt_secret, encrypt_secret


class OneCCredentialError(ValueError):
    """Fixed message that never contains user input or ciphertext."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class BasicCredentials:
    login: str
    password: str

    def __repr__(self):
        return "BasicCredentials(<redacted>)"


def _validate(login, password):
    if (
        not isinstance(login, str)
        or not isinstance(password, str)
        or not login
        or not password
        or len(login) > 320
        or len(password) > 4096
        or ":" in login
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in login + password)
    ):
        raise OneCCredentialError("invalid_credentials")
    return BasicCredentials(login, password)


def seal_basic_credentials(login, password):
    """Return a Fernet ciphertext suitable for WorkspaceConnectionSecret."""
    credentials = _validate(login, password)
    payload = json.dumps(
        {"version": 1, "login": credentials.login, "password": credentials.password}, ensure_ascii=True
    )
    try:
        return encrypt_secret(payload)
    except (ValueError, TypeError, RuntimeError, UnicodeError):
        raise OneCCredentialError("secret_unavailable") from None


def open_basic_credentials(ciphertext):
    """Read a known-format bundle; unknown or corrupt payloads fail closed."""
    if not isinstance(ciphertext, str) or not ciphertext:
        raise OneCCredentialError("secret_unavailable")
    try:
        data = json.loads(decrypt_secret(ciphertext))
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "login", "password"}
            or type(data["version"]) is not int
        ):
            raise ValueError
        if data["version"] != 1:
            raise ValueError
        return _validate(data["login"], data["password"])
    except (ValueError, TypeError, KeyError, RuntimeError, UnicodeError):
        raise OneCCredentialError("secret_unavailable") from None
