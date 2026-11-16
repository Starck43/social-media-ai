"""Internal setup primitive for a private read-only Yandex connection.

No route calls this service yet. A future UI must obtain explicit informed
consent, enforce CSRF and disclose that the OAuth read scope covers the disk.
Saving credentials never contacts the provider.
"""

from __future__ import annotations

import secrets
import unicodedata

from app.core.database import async_session_maker
from app.integrations.catalog import CATALOG_VERSION
from app.integrations.file_workspace.policy import ReadOnlyFolderPolicy
from app.models import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import _authorize
from app.utils.crypto import encrypt_secret


class FolderConnectionInputError(ValueError):
    """Sanitized setup validation error; never includes a supplied value."""


def _validate_setup(label: str, root: str, oauth_token: str) -> tuple[str, str, str]:
    if not isinstance(label, str):
        raise FolderConnectionInputError("invalid_label")
    label = label.strip()
    if len(label) > 100 or any(unicodedata.category(char).startswith("C") for char in label):
        raise FolderConnectionInputError("invalid_label")
    if not isinstance(oauth_token, str) or not 1 <= len(oauth_token) <= 2048:
        raise FolderConnectionInputError("invalid_token")
    if any(not 33 <= ord(char) <= 126 for char in oauth_token):
        raise FolderConnectionInputError("invalid_token")
    try:
        ReadOnlyFolderPolicy(root)
    except ValueError:
        raise FolderConnectionInputError("invalid_root") from None
    return label or "Яндекс.Диск", root, oauth_token


async def create_yandex_connection(*, tenant_id: int, actor_id: int, label: str, root: str, oauth_token: str) -> dict:
    """Create an unverified personal connection and selected read-only root.

    The caller owns user consent and CSRF. Authorization is rechecked from the
    database before writing; no operator bypass, provider call, write scope,
    filesystem access or verification claim is made here. The random internal
    account key is not a verified provider identity and allows distinct mounts.
    """
    label, root, token = _validate_setup(label, root, oauth_token)
    async with async_session_maker() as session, session.begin():
        await _authorize(session, tenant_id, actor_id, write=True)
        connection = WorkspaceConnection(
            tenant_id=tenant_id,
            owner_user_id=actor_id,
            platform="yandex_disk",
            provider_id="yandex_disk",
            auth_method="oauth_token",
            schema_version=CATALOG_VERSION,
            label=label,
            account_key="file-conn:" + secrets.token_hex(16),
            config={"file_policy": {"version": 1, "root": root, "restrictions": []}},
            is_active=True,
            state="unverified",
        )
        session.add(connection)
        await session.flush()
        session.add(WorkspaceConnectionSecret(connection_id=connection.id, secret_encrypted=encrypt_secret(token)))
        await session.flush()
        return {"id": connection.id, "label": connection.label, "root": root, "state": "unverified"}
