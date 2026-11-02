"""Owner-bound, read-only metadata access for a configured Yandex connection.

There is intentionally no public route or connection-configuration flow yet.
The stored policy must be written by a future authorized settings flow, never
constructed from a request. No share policy is supported in this first slice.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.database import async_session_maker
from app.integrations.catalog import CATALOG_VERSION
from app.integrations.file_workspace.policy import FileOperation, FolderRestriction, ReadOnlyFolderPolicy
from app.integrations.file_workspace.yandex_disk import DiskPage, MetadataError, list_folder
from app.models import WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize, _connection
from app.utils.crypto import decrypt_secret

_MAX_RESTRICTIONS = 100
_TOTAL_TIMEOUT = 15.0


def _stored_policy(config: object) -> ReadOnlyFolderPolicy:
    """Strictly compile a trusted stored policy; malformed data denies access."""
    try:
        if not isinstance(config, dict):
            raise ValueError
        data = config.get("file_policy")
        if not isinstance(data, dict) or set(data) != {"version", "root", "restrictions"}:
            raise ValueError
        if type(data["version"]) is not int or data["version"] != 1 or not isinstance(data["restrictions"], list):
            raise ValueError
        if len(data["restrictions"]) > _MAX_RESTRICTIONS:
            raise ValueError
        restrictions = []
        for item in data["restrictions"]:
            if not isinstance(item, dict) or set(item) != {"path", "deny"} or not isinstance(item["deny"], list):
                raise ValueError
            denied = item["deny"]
            if len(denied) > 2 or any(type(value) is not str or value not in {"list", "read"} for value in denied):
                raise ValueError
            if len(set(denied)) != len(denied):
                raise ValueError
            restrictions.append(FolderRestriction(item["path"], frozenset(FileOperation(value) for value in denied)))
        return ReadOnlyFolderPolicy(data["root"], tuple(restrictions))
    except (ValueError, TypeError, KeyError):
        raise ConnectionDenied() from None


async def list_owned_folder(
    *,
    tenant_id: int,
    actor_id: int,
    connection_id: int,
    folder: str,
    client: httpx.AsyncClient,
    offset: int = 0,
    limit: int = 100,
) -> DiskPage:
    """List one authorized page, re-reading membership, connection and policy.

    `client` is application-controlled, not a request parameter. Shared locks on
    actor/membership and an exclusive lock on the connection remain held during
    bounded network work, serializing revocation/connection changes. The caller
    must supply an outbound-network-reviewed client with safe DNS/proxy policy.
    This service does not create, update or validate provider credentials.
    """
    async with async_session_maker() as session, session.begin():
        await _authorize(session, tenant_id, actor_id, require_source_view=True)
        connection = await _connection(session, tenant_id, actor_id, connection_id)
        if (
            not connection.is_active
            or connection.state not in {"unverified", "verified"}
            or connection.provider_id != "yandex_disk"
            or connection.platform != "yandex_disk"
            or connection.auth_method != "oauth_token"
            or connection.schema_version != CATALOG_VERSION
        ):
            raise ConnectionDenied()
        policy = _stored_policy(connection.config)
        if not policy.permits(folder, FileOperation.LIST):
            raise ConnectionDenied()
        secret = await session.get(WorkspaceConnectionSecret, connection.id)
        if not secret or not secret.secret_encrypted:
            raise ConnectionDenied()
        try:
            token = decrypt_secret(secret.secret_encrypted)
        except (ValueError, RuntimeError, TypeError, AttributeError):
            raise ConnectionDenied() from None
        try:
            async with asyncio.timeout(_TOTAL_TIMEOUT):
                return await list_folder(client, token=token, folder=folder, policy=policy, offset=offset, limit=limit)
        except TimeoutError:
            raise MetadataError("provider_unavailable") from None
