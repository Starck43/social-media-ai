"""Runtime job handler for incremental email IMAP synchronization (`comms.email.sync`, P15-T01)."""

from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.integrations.catalog import WORKSPACE_PROVIDER_IDS
from app.integrations.email.imap_sync import (
    ImapSyncService,
    imap_sync_service,
    validate_folder_list,
)
from app.models.integrations.connection import WorkspaceConnection

JOB_NAME = "comms.email.sync"


async def handle_email_sync(
    payload: dict[str, Any],
    *,
    sync_service: ImapSyncService = imap_sync_service,
    session=None,
) -> dict[str, Any]:
    """Execute `comms.email.sync` for a workspace's active email connection(s)."""
    tenant_id = current_tenant_id()
    if tenant_id is None or is_bypass():
        raise TenantContextError("Email IMAP sync job requires a tenant-scoped workspace context")

    if session is None:
        async with async_session_maker() as owned, owned.begin():
            return await handle_email_sync(payload, sync_service=sync_service, session=owned)

    connection_id = payload.get("connection_id")
    raw_folders = payload.get("folders")
    folders = validate_folder_list(raw_folders) if isinstance(raw_folders, (list, tuple)) and raw_folders else None

    stmt = select(WorkspaceConnection).where(
        WorkspaceConnection.tenant_id == tenant_id,
        WorkspaceConnection.provider_id.in_(tuple(WORKSPACE_PROVIDER_IDS)),
        WorkspaceConnection.is_active.is_(True),
    )
    if connection_id is not None:
        stmt = stmt.where(WorkspaceConnection.id == int(connection_id))
    stmt = stmt.order_by(WorkspaceConnection.id)

    connections = list((await session.execute(stmt)).scalars().all())
    runs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for conn in connections:
        # One savepoint per connection (B-13): a failure here must not discard rows another
        # connection has already ingested in this run.
        savepoint = session.begin_nested()
        if inspect.isawaitable(savepoint):
            savepoint = await savepoint
        try:
            result = await sync_service.sync_connection(
                session,
                connection_id=conn.id,
                folders=folders,
            )
            runs.append(result)
        except Exception as exc:  # noqa: BLE001
            rollback = getattr(savepoint, "rollback", None)
            if rollback is not None:
                rolled_back = rollback()
                if inspect.isawaitable(rolled_back):
                    await rolled_back
            if connection_id is not None:
                raise
            errors.append(
                {
                    "connection_id": conn.id,
                    "error": getattr(exc, "code", exc.__class__.__name__),
                }
            )
        else:
            commit = getattr(savepoint, "commit", None)
            if commit is not None:
                committed = commit()
                if inspect.isawaitable(committed):
                    await committed

    return {
        "job": JOB_NAME,
        "tenant_id": tenant_id,
        "connections_synced": len(runs),
        "runs": runs,
        "errors": errors,
    }
