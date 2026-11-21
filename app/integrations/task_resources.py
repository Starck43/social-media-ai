"""Resolve a selection under current membership and provider policy without external I/O."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select

from app.core.config import settings
from app.integrations.catalog import CATALOG_VERSION
from app.integrations.catalog.task_capabilities import CAPABILITIES, Direction, ResourceKind
from app.integrations.email.imap_sync import get_imap_folders
from app.integrations.file_workspace.policy import FileOperation
from app.models import Platform, Source, WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import _authorize, _connection
from app.services.file_workspace.yandex_reader import _stored_policy
from app.services.local_connector.runtime import get_gateway
from app.services.local_connector.wire import ConnectorUnavailable
from app.services.planner.tasks import planner_service
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import _inspect_source_grant, source_grant_selection
from app.services.social.vk_access import VKSelection
from app.services.social.vk_connections import VKConnectionDenied, check_selected_access
from app.types import ActionType, PlatformType


@dataclass(frozen=True)
class ResourceSelection:
    kind: ResourceKind
    resource_id: int
    path: str | None = None
    vk_access: VKSelection | None = None


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
    INSTALLATION = "installation_required"


async def _social_issue(session, tenant_id, actor_id, capability, selection):
    if selection.path is not None:
        return ResolutionIssue.SELECT_RESOURCE
    platform_type = PlatformType.VK if capability.provider == "vk" else PlatformType.TELEGRAM
    source = await session.scalar(
        select(Source)
        .join(Platform)
        .where(
            Source.id == selection.resource_id,
            Source.tenant_id == tenant_id,
            Source.is_active.is_(True),
            Source.owner_task_id.is_(None),
            Platform.platform_type == platform_type,
            Platform.is_active.is_(True),
        )
    )
    if source is None:
        return ResolutionIssue.UNAVAILABLE
    if platform_type is PlatformType.VK:
        if selection.vk_access is None:
            return ResolutionIssue.SELECT_RESOURCE
        if source.owner_user_id not in (None, actor_id) or (source.params or {}).get("token_owner") not in (
            None,
            actor_id,
        ):
            return ResolutionIssue.UNAVAILABLE
        if selection.vk_access.mode == "api" and any(
            not getattr(settings, key, None) for key in capability.installation_keys
        ):
            return ResolutionIssue.INSTALLATION
        decision = await check_selected_access(
            selection.vk_access,
            tenant_id=tenant_id,
            actor_id=actor_id,
            required_methods=frozenset({"wall.get"}),
        )
        if not decision.allowed:
            return ResolutionIssue.REAUTH if selection.vk_access.mode == "user" else ResolutionIssue.UNAVAILABLE
    else:
        if not settings.TELEGRAM_ACCOUNT_RUNTIME_ENABLED:
            return ResolutionIssue.UNAVAILABLE
        grant = source_grant_selection(source)
        if grant.owner_user_id != actor_id:
            return ResolutionIssue.UNAVAILABLE
        await _inspect_source_grant(session, grant)
    return None


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
            or (
                selection.vk_access is not None
                and (capability.provider != "vk" or not isinstance(selection.vk_access, VKSelection))
            )
            or selection.kind != capability.kind
            or type(selection.resource_id) is not int
            or selection.resource_id <= 0
        ):
            return ResolutionIssue.SELECT_RESOURCE
        if capability.kind == ResourceKind.SOCIAL_SOURCE:
            issue = await _social_issue(session, tenant_id, actor_id, capability, selection)
            if issue is not None:
                return issue
        elif capability.kind == ResourceKind.PAIRED_ROOT:
            if not selection.path:
                return ResolutionIssue.SELECT_RESOURCE
            await get_gateway().store.authorize(
                owner_id=actor_id, device_id=selection.resource_id, roots=(selection.path,)
            )
        else:
            if capability.kind == ResourceKind.PORTAL:
                await planner_service._actor(session, ActionType.VIEW)
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
    except (AuthorizationUnavailable, VKConnectionDenied):
        return ResolutionIssue.REAUTH
    except (PermissionError, ConnectorUnavailable, ValueError):
        return ResolutionIssue.UNAVAILABLE
