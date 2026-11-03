"""Fresh, metadata-only checks for an explicitly pinned Telegram source grant.

A snapshot is NOT an execution permit or a shared-session lease. No secret is
selected/decrypted by the metadata API. Only the internal lock-holder resolver
opts into envelope loading. The collector remains gated on request-boundary
rechecks, bounded runtime ownership and settlement fencing together.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Platform, Source, Tenant, TenantUser, User
from app.models import WorkspaceAuthorizationGrant as Grant
from app.models import WorkspaceRole
from app.services.social.connector_authorizations import AuthorizationUnavailable, _decode_telegram_material
from app.types import ActionType, PlatformType

_BINDING = "telegram_authorization"


def _positive(value):
    if type(value) is not int or value <= 0:
        raise AuthorizationUnavailable()
    return value


@dataclass(frozen=True, repr=False)
class SourceGrantSelection:
    """Non-secret pin; queued work must not silently follow an edited source."""

    source_id: int
    tenant_id: int
    platform_id: int
    external_id: str
    owner_user_id: int
    grant_id: int
    authorization_revision: int

    def __post_init__(self):
        for value in (
            self.source_id,
            self.tenant_id,
            self.platform_id,
            self.owner_user_id,
            self.grant_id,
            self.authorization_revision,
        ):
            _positive(value)
        if not isinstance(self.external_id, str) or not self.external_id or len(self.external_id) > 100:
            raise AuthorizationUnavailable()


def source_grant_selection(source) -> SourceGrantSelection:
    """Parse an existing source's explicit selection; never discover a fallback."""
    params = getattr(source, "params", None)
    if not isinstance(params, dict) or params.get("mode", "auto") not in ("auto", "user"):
        raise AuthorizationUnavailable()
    binding = params.get(_BINDING)
    if not isinstance(binding, dict) or set(binding) != {"grant_id", "authorization_revision"}:
        raise AuthorizationUnavailable()
    owner = params.get("token_owner")
    if owner is None:
        owner = getattr(source, "owner_user_id", None)
    return SourceGrantSelection(
        source_id=getattr(source, "id", None),
        tenant_id=getattr(source, "tenant_id", None),
        platform_id=getattr(source, "platform_id", None),
        external_id=getattr(source, "external_id", None),
        owner_user_id=owner,
        grant_id=binding["grant_id"],
        authorization_revision=binding["authorization_revision"],
    )


@dataclass(frozen=True)
class SourceGrantSnapshot:
    """A point-in-time fact, never a cached authorization to contact Telegram."""

    selection: SourceGrantSelection
    authorization_id: int


def _validate_selection_context(selection):
    if type(selection) is not SourceGrantSelection:
        raise AuthorizationUnavailable()
    if is_bypass() or type(current_tenant_id()) is not int or current_tenant_id() != selection.tenant_id:
        raise AuthorizationUnavailable()


async def _read_source_grant(
    session, selection: SourceGrantSelection, *, include_authorization=False, expected_authorization_id=None
):
    """One SQL snapshot for source/owner/grant/account; secret opt-in is internal.

    The guarded runtime resolver explicitly selects the authorization entity;
    metadata callers select only its ID. Both paths use the same predicates and
    refresh the identity map in a READ COMMITTED lock transaction.
    """
    _validate_selection_context(selection)
    statement = (
        select(
            Source,
            Authorization if include_authorization else Authorization.id.label("authorization_id"),
            WorkspaceRole.codename.label("owner_role"),
        )
        .join(Platform, Platform.id == Source.platform_id)
        .join(Tenant, Tenant.id == Source.tenant_id)
        .join(User, User.id == selection.owner_user_id)
        .join(
            TenantUser,
            and_(TenantUser.tenant_id == Source.tenant_id, TenantUser.user_id == User.id),
        )
        .join(WorkspaceRole, WorkspaceRole.id == TenantUser.role_id)
        .join(
            Grant,
            and_(Grant.id == selection.grant_id, Grant.tenant_id == Source.tenant_id, Grant.owner_user_id == User.id),
        )
        .join(
            Authorization,
            and_(Authorization.id == Grant.authorization_id, Authorization.owner_user_id == Grant.owner_user_id),
        )
        .where(
            Source.id == selection.source_id,
            Source.tenant_id == selection.tenant_id,
            Source.is_active.is_(True),
            Platform.platform_type == PlatformType.TELEGRAM,
            Platform.is_active.is_(True),
            Tenant.is_active.is_(True),
            User.is_active.is_(True),
            TenantUser.channel == "web",
            TenantUser.is_active.is_(True),
            Grant.is_active.is_(True),
            Grant.authorization_revision == selection.authorization_revision,
            Authorization.provider_id == "telegram",
            Authorization.auth_method == "mtproto",
            Authorization.state == "active",
            Authorization.revision == selection.authorization_revision,
            Authorization.secret_encrypted != "",
        )
    )
    if include_authorization:
        # Never even select another account's envelope under the old account lock
        # if a grant was rebound after lock acquisition.
        statement = statement.where(Authorization.id == _positive(expected_authorization_id))
    # A lock holder may inspect repeatedly in one READ COMMITTED transaction.
    # Refresh ORM identities too; a new SQL row must not reuse stale Source.params.
    row = (await session.execute(statement.execution_options(populate_existing=True))).one_or_none()
    if row is None:
        raise AuthorizationUnavailable()
    source, authorization, owner_role = row
    if source_grant_selection(source) != selection or ("source", ActionType.VIEW) not in workspace_grants(owner_role):
        raise AuthorizationUnavailable()
    authorization_id = authorization.id if include_authorization else authorization
    return SourceGrantSnapshot(selection, authorization_id), authorization


async def _inspect_source_grant(session, selection: SourceGrantSelection) -> SourceGrantSnapshot:
    """Metadata only; initiator/payer rights and provider health remain separate."""
    snapshot, _ = await _read_source_grant(session, selection)
    return snapshot


async def _resolve_source_grant_material(session, expected: SourceGrantSnapshot):
    """Internal lock-holder helper, never an endpoint or standalone I/O permit.

    Read the exact account and its envelope in the same SQL snapshot as consent.
    The guard must check the returned account pin and recheck after decryption.
    """
    snapshot, authorization = await _read_source_grant(
        session, expected.selection, include_authorization=True, expected_authorization_id=expected.authorization_id
    )
    if snapshot != expected:
        raise AuthorizationUnavailable()
    return snapshot, _decode_telegram_material(authorization)


async def inspect_source_grant(selection: SourceGrantSelection) -> SourceGrantSnapshot:
    """Standalone metadata snapshot, never an execution permit or account lock."""
    _validate_selection_context(selection)
    async with async_session_maker() as session, session.begin():
        return await _inspect_source_grant(session, selection)
