"""Purpose-separated encrypted private envelopes; injected application key only."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet

from app.integrations.file_workspace.private_payload import private_payload_is_canonical


class PrivateCipher:
    """No settings/logging or fallback key. Rotation makes old ciphertext unavailable."""

    def __init__(self, key: str):
        try:
            if type(key) is not str or len(key) != 44:
                raise ValueError
            raw = base64.b64decode(key.encode("ascii"), altchars=b"-_", validate=True)
            if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode() != key:
                raise ValueError
            derived = hmac.new(raw, b"file-private-insight-v1", hashlib.sha256).digest()
            self._cipher = Fernet(base64.urlsafe_b64encode(derived))
        except Exception:
            pass
        else:
            return
        raise ValueError("private_cipher_unavailable")

    def encrypt(self, payload: bytes) -> str:
        if not private_payload_is_canonical(payload):
            raise ValueError("private_cipher_unavailable")
        return self._cipher.encrypt(payload).decode("ascii")

    def decrypt(self, token: str) -> bytes:
        try:
            if type(token) is not str or not 1 <= len(token) <= 90000:
                raise ValueError
            payload = self._cipher.decrypt(token.encode("ascii"))
            if not private_payload_is_canonical(payload):
                raise ValueError
            return payload
        except Exception:
            pass
        raise ValueError("private_cipher_unavailable")
