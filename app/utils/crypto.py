"""Fernet helpers for per-tenant secrets: one env key, reversible encryption.

`CREDENTIALS_KEY` must be a urlsafe-base64 32-byte key (`Fernet.generate_key()`).
Rotation: set a new key and re-encrypt (there is deliberately no multi-key
support — credentials are few and re-encryption is a one-off script).
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    key = settings.CREDENTIALS_KEY
    if not key:
        raise RuntimeError("CREDENTIALS_KEY is not set — cannot encrypt secrets")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise RuntimeError("CREDENTIALS_KEY is not a valid Fernet key") from e


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage in `tenant_credentials.secret_encrypted`."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Decrypt a secret. Raises ValueError on a wrong/rotated key."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Cannot decrypt secret (wrong CREDENTIALS_KEY?)") from e
