"""Resolve a selection under current membership and provider policy without external I/O."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select

from app.integrations.catalog import CATALOG_VERSION
from app.integrations.catalog.task_capabilities import CAPABILITIES, Direction, ResourceKind
from app.integrations.email.imap_sync import get_imap_folders
from app.integrations.file_workspace.policy import FileOperation
from app.models import WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize, _connection
from app.services.file_workspace.yandex_reader import _stored_policy
from app.services.local_connector.runtime import get_gateway
from app.services.local_connector.wire import ConnectorUnavailable


@dataclass(frozen=True)
class ResourceSelection:
    kind: ResourceKind
    resource_id: int
    path: str | None = None


@dataclass(frozen=True)
class ScopedResource:
    tenant_id: int
    actor_id: int
    capability_id: str
    selection: ResourceSelection


class ResolutionIssue(StrEnum):
    UNSUPPORTED = "unsupported_capability"
    READ_ONLY = "read_only"
    SELECT_RESOURCE = "select_resource"
    UNAVAILABLE = "resource_unavailable"
    REAUTH = "reauth_required"


async def resolve_task_resource(
    session,
    *,
    tenant_id: int,
    actor_id: int,
    capability_id: str,
    direction: Direction,
    selection: ResourceSelection | None,
) -> ScopedResource | ResolutionIssue:
    """Re-run at execution; owning handlers retain their checks and secret access."""
    try:
        await _authorize(session, tenant_id, actor_id, require_source_view=True)
        capability = CAPABILITIES.get(capability_id)
        if capability is None or capability.handler is None:
            return ResolutionIssue.UNSUPPORTED
        if direction != capability.direction:
            return ResolutionIssue.READ_ONLY
        if (
            selection is None
            or selection.kind != capability.kind
            or type(selection.resource_id) is not int
            or selection.resource_id <= 0
        ):
            return ResolutionIssue.SELECT_RESOURCE
        if capability.kind == ResourceKind.PAIRED_ROOT:
            if not selection.path:
                return ResolutionIssue.SELECT_RESOURCE
            await get_gateway().store.authorize(
                owner_id=actor_id, device_id=selection.resource_id, roots=(selection.path,)
            )
        else:
            row = await _connection(session, tenant_id, actor_id, selection.resource_id)
            if (
                row.provider_id != capability.provider
                or row.schema_version != CATALOG_VERSION
                or (capability.method is not None and row.auth_method != capability.method)
                or not row.is_active
            ):
                return ResolutionIssue.UNAVAILABLE
            if row.state == "reauth":
                return ResolutionIssue.REAUTH
            if row.state != "verified":
                return ResolutionIssue.UNAVAILABLE
            if capability.kind == ResourceKind.PORTAL:
                consent = (row.config or {}).get("task_source", {})
                if (
                    selection.path is not None
                    or not isinstance(consent, dict)
                    or consent.get("read_consent") is not True
                    or consent.get("read_consented_by") != actor_id
                ):
                    return ResolutionIssue.UNAVAILABLE
            elif capability.kind == ResourceKind.MAIL_FOLDER:
                if selection.path not in get_imap_folders(row):
                    return ResolutionIssue.SELECT_RESOURCE
            elif capability.kind == ResourceKind.DISK_FOLDER:
                policy = _stored_policy(row.config)
                if selection.path is None:
                    return ResolutionIssue.SELECT_RESOURCE
                if not policy.permits(selection.path, FileOperation.LIST):
                    return ResolutionIssue.UNAVAILABLE
            exists = await session.scalar(
                select(WorkspaceConnectionSecret.connection_id).where(WorkspaceConnectionSecret.connection_id == row.id)
            )
            if exists is None:
                return ResolutionIssue.REAUTH
        return ScopedResource(tenant_id, actor_id, capability_id, selection)
    except (ConnectionDenied, ConnectorUnavailable, ValueError):
        return ResolutionIssue.UNAVAILABLE
