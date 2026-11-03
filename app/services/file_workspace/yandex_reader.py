"""Owner-bound, read-only metadata access for a configured Yandex connection.

There is intentionally no public folder-browsing route or job yet. The stored
policy is loaded from the owned connection, never constructed from a request.
No share policy is supported in this first slice.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx

from app.core.database import async_session_maker
from app.integrations.catalog import CATALOG_VERSION
from app.integrations.file_workspace.policy import FileOperation, FolderRestriction, ReadOnlyFolderPolicy, _components
from app.integrations.file_workspace.yandex_disk import DiskPage, MetadataError, _disk_path, list_folder
from app.integrations.file_workspace.yandex_http import YandexMetadataClient
from app.models import WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize, _connection
from app.utils.crypto import decrypt_secret

_MAX_RESTRICTIONS = 100
# One absolute budget for authorization, the bounded read and the lock wait. The
# transport has no timer of its own, so this value is the whole exchange deadline.
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
    folder: str | None = None,
    client: httpx.AsyncClient | YandexMetadataClient | None = None,
    offset: int = 0,
    limit: int = 100,
) -> DiskPage:
    """List one authorized page, re-reading membership, connection and policy.

    By default this uses the fixed-endpoint, pinned transport. An injected
    `client` is trusted application/test code, never a request parameter. Shared
    locks on actor/membership and an exclusive lock on the connection remain held during
    bounded network work, serializing revocation/connection changes; disabling the
    connection therefore waits for at most one in-flight read (``_TOTAL_TIMEOUT``).
    The caller
    must not expose a custom client to external input. Production browsing remains
    gated on combined transport and route review. This service does not create,
    update or validate provider credentials.
    """
    if client is None:
        client = YandexMetadataClient()
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
        # The root comes from the freshly loaded authorized connection, never
        # a posted tenant, URL or a previously cached policy.
        root = connection.config["file_policy"]["root"]
        if folder is None:
            folder = root
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
                page = await list_folder(client, token=token, folder=folder, policy=policy, offset=offset, limit=limit)
                return replace(
                    page, current_path=_disk_path(_components(folder)), root_path=_disk_path(_components(root))
                )
        except TimeoutError:
            raise MetadataError("provider_unavailable") from None
