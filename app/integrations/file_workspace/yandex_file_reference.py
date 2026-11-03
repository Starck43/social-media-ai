"""Short-lived opaque file selectors; not file IDs or authorization grants.

No network, storage or browser route is implemented here. A caller may issue a
selector only for a validated provider listing after current owner/tenant
checks. At use time it must load current membership, owner, connection, policy
and provider file identity again, and recheck throughout any download. A path
can be replaced or renamed; this token is not a stable identity or revision.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings
from app.integrations.file_workspace.policy import FileOperation, ReadOnlyFolderPolicy, _components
from app.integrations.file_workspace.yandex_disk import DiskItem, _disk_path

_TTL_SECONDS = 300
_MAX_TOKEN = 8192
_MAX_PATH_BYTES = 4096
_PURPOSE = "yandex-file-selector-v1"


class FileReferenceError(Exception):
    """Generic failure without file paths, context, keys or selectors."""


@dataclass(frozen=True)
class FileReference:
    # Sensitive metadata, never show it in default repr or logs.
    path: str = field(repr=False)


def _clock() -> int:
    return int(time.time())


def _fernet() -> Fernet:
    """Derive a purpose-separated key; key rotation invalidates old selectors."""
    key = settings.CREDENTIALS_KEY
    if not isinstance(key, str):
        raise ValueError
    # Fernet validates encoding and length before key derivation.
    Fernet(key.encode("ascii"))
    raw = base64.urlsafe_b64decode(key.encode("ascii"))
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_PURPOSE.encode()).derive(raw)
    return Fernet(base64.urlsafe_b64encode(derived))


def _context(tenant_id: int, actor_id: int, connection_id: int, session_id: str) -> None:
    if any(type(value) is not int or not 0 < value < 2**63 for value in (tenant_id, actor_id, connection_id)):
        raise ValueError
    if not isinstance(session_id, str) or not 16 <= len(session_id) <= 200 or not session_id.isascii():
        raise ValueError
    if any(not (char.isalnum() or char in "_-") for char in session_id):
        raise ValueError


def _file_path(item: DiskItem, policy: ReadOnlyFolderPolicy) -> str:
    if not isinstance(item, DiskItem) or item.kind != "file" or not isinstance(policy, ReadOnlyFolderPolicy):
        raise ValueError
    parts = _components(item.path)
    if not parts or item.name != parts[-1]:
        raise ValueError
    path = _disk_path(parts)
    if len(path.encode("utf-8")) > _MAX_PATH_BYTES:
        raise ValueError
    if not policy.permits(path, FileOperation.LIST) or not policy.permits(path, FileOperation.READ):
        raise ValueError
    return path


def issue_file_reference(
    *, tenant_id: int, actor_id: int, connection_id: int, session_id: str, item: DiskItem, policy: ReadOnlyFolderPolicy
) -> str:
    """Issue only after authenticating the caller and validating provider data."""
    try:
        _context(tenant_id, actor_id, connection_id, session_id)
        path = _file_path(item, policy)
        data = {
            "purpose": _PURPOSE,
            "tenant": tenant_id,
            "actor": actor_id,
            "connection": connection_id,
            "session": session_id,
            "path": path,
        }
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        token = _fernet().encrypt_at_time(payload, _clock()).decode("ascii")
        if len(token) > _MAX_TOKEN:
            raise ValueError
        return token
    except (ValueError, TypeError, RuntimeError, UnicodeError, OverflowError):
        raise FileReferenceError("file_unavailable") from None


def resolve_file_reference(
    *, tenant_id: int, actor_id: int, connection_id: int, session_id: str, token: str, policy: ReadOnlyFolderPolicy
) -> FileReference:
    """Decode under the current context and policy; not a substitute for auth/stat."""
    try:
        _context(tenant_id, actor_id, connection_id, session_id)
        if not isinstance(token, str) or not token.isascii() or not 1 <= len(token) <= _MAX_TOKEN:
            raise ValueError
        plaintext = _fernet().decrypt_at_time(token.encode("ascii"), ttl=_TTL_SECONDS, current_time=_clock())
        data = json.loads(plaintext)
        if not isinstance(data, dict) or set(data) != {"purpose", "tenant", "actor", "connection", "session", "path"}:
            raise ValueError
        if data["purpose"] != _PURPOSE:
            raise ValueError
        for name, expected in (("tenant", tenant_id), ("actor", actor_id), ("connection", connection_id)):
            if type(data[name]) is not int or data[name] != expected:
                raise ValueError
        if not isinstance(data["session"], str) or not hmac.compare_digest(data["session"], session_id):
            raise ValueError
        parts = _components(data["path"])
        if not parts:
            raise ValueError
        path = _file_path(DiskItem(data["path"], parts[-1], "file"), policy)
        return FileReference(path)
    except (InvalidToken, ValueError, TypeError, KeyError, RuntimeError, UnicodeError, OverflowError, RecursionError):
        raise FileReferenceError("file_unavailable") from None
