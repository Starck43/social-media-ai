"""Runtime job handler for Bitrix24 read-only incremental synchronization (`crm.bitrix24.sync`, P14-T02)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.models.crm.entity import CRM_ENTITY_KINDS
from app.models.integrations.connection import WorkspaceConnection
from app.services.crm.sync import BitrixSyncService, bitrix_sync_service

JOB_NAME = "crm.bitrix24.sync"


async def handle_crm_sync(
    payload: dict[str, Any],
    *,
    sync_service: BitrixSyncService = bitrix_sync_service,
    session=None,
) -> dict[str, Any]:
    """Execute `crm.bitrix24.sync` for a workspace's active Bitrix24 connection(s)."""
    tenant_id = current_tenant_id()
    if tenant_id is None or is_bypass():
        raise TenantContextError("Bitrix24 sync job requires a tenant-scoped workspace context")

    if session is None:
        async with async_session_maker() as owned, owned.begin():
            return await handle_crm_sync(payload, sync_service=sync_service, session=owned)

    connection_id = payload.get("connection_id")
    raw_kinds = payload.get("entity_kinds")
    if isinstance(raw_kinds, (list, tuple)):
        entity_kinds = tuple(str(k).strip() for k in raw_kinds if str(k).strip() in CRM_ENTITY_KINDS)
    else:
        entity_kinds = CRM_ENTITY_KINDS
    if not entity_kinds:
        entity_kinds = CRM_ENTITY_KINDS

    stmt = select(WorkspaceConnection).where(
        WorkspaceConnection.tenant_id == tenant_id,
        WorkspaceConnection.provider_id == "bitrix24",
        WorkspaceConnection.is_active.is_(True),
    )
    if connection_id is not None:
        stmt = stmt.where(WorkspaceConnection.id == int(connection_id))
    stmt = stmt.order_by(WorkspaceConnection.id)

    connections = list((await session.execute(stmt)).scalars().all())
    runs: list[dict[str, Any]] = []
    for conn in connections:
        actor_id = int(payload.get("actor_id") or conn.owner_user_id)
        result = await sync_service.sync_connection(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=conn.id,
            entity_kinds=entity_kinds,
            session=session,
        )
        runs.append(result)

    return {
        "job": JOB_NAME,
        "tenant_id": tenant_id,
        "connections_synced": len(runs),
        "runs": runs,
    }
