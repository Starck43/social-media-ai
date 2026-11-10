"""Purpose-separated authenticated encryption for rendered export bytes.

This is a pure envelope helper, not a storage, authorization or approval service.
A caller must independently enforce tenant, revision and permission boundaries.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ExportArtifactUnavailable(ValueError):
    """Opaque refusal for malformed, tampered, oversized or unreadable artifacts."""


@dataclass(frozen=True)
class EncryptedExport:
    token: str = field(repr=False)
    payload_sha256: str
    size_bytes: int


class ExportCipher:
    """Encrypt bounded bytes with a key domain-separated from private insights.

    Key rotation is deliberately not implemented: old tokens fail closed until a
    separately authorized re-encryption mechanism is provided.
    """

    def __init__(self, key: str, *, max_bytes: int):
        try:
            if type(key) is not str or len(key) != 44 or type(max_bytes) is not int or max_bytes <= 0:
                raise ValueError
            raw = base64.b64decode(key.encode("ascii"), altchars=b"-_", validate=True)
            if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode("ascii") != key:
                raise ValueError
            derived = hmac.new(raw, b"document-export-artifact-v1", hashlib.sha256).digest()
            self._cipher = Fernet(base64.urlsafe_b64encode(derived))
            self._max_bytes = max_bytes
        except (ValueError, TypeError, UnicodeError):
            raise ExportArtifactUnavailable("export_artifact_unavailable") from None

    def seal(self, payload: bytes) -> EncryptedExport:
        if type(payload) is not bytes or not 1 <= len(payload) <= self._max_bytes:
            raise ExportArtifactUnavailable("export_artifact_unavailable")
        return EncryptedExport(
            token=self._cipher.encrypt(payload).decode("ascii"),
            payload_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )

    def open(self, token: str, *, expected_hash: str, expected_size: int) -> bytes:
        # Bound ciphertext before decrypting to avoid processing arbitrarily large inputs.
        # Fernet token bytes: 57 framing bytes + PKCS7-padded payload, then base64.
        maximum_token_length = 4 * ((57 + (self._max_bytes // 16 + 1) * 16 + 2) // 3)
        if (
            type(token) is not str
            or not 1 <= len(token) <= maximum_token_length
            or type(expected_size) is not int
            or not 1 <= expected_size <= self._max_bytes
            or type(expected_hash) is not str
            or not _HASH.fullmatch(expected_hash)
        ):
            raise ExportArtifactUnavailable("export_artifact_unavailable")
        try:
            payload = self._cipher.decrypt(token.encode("ascii"))
        except (InvalidToken, UnicodeError, ValueError, TypeError):
            raise ExportArtifactUnavailable("export_artifact_unavailable") from None
        actual_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        if len(payload) != expected_size or not hmac.compare_digest(actual_hash, expected_hash):
            raise ExportArtifactUnavailable("export_artifact_unavailable")
        return payload
