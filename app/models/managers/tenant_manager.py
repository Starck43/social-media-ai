"""Tenant managers: invites, channel binding, credentials.

Invite codes are the only write path that crosses tenant boundaries: redeeming
a code creates a TenantUser + TenantChannel row for the caller's chat. Codes
are stored as SHA-256 hashes, checked for expiry/use-count, and the use count
is bumped atomically-ish (single row update; double-redeem of a 1-use code is
caught by the max_uses check on the second call).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.access_catalog import workspace_grants
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..tenancy import Tenant, TenantChannel, TenantInvite, TenantUser

logger = logging.getLogger(__name__)


def hash_invite_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


WEB_CHANNEL = "web"


def _resolve_role_codename(role: str | None) -> str | None:
    from app.types import WorkspaceRoleType

    if role is None:
        return None
    value = {"member": "viewer", "superuser": "owner"}.get(str(role).lower(), str(role).lower())
    try:
        return WorkspaceRoleType(value).value
    except ValueError:
        return None


async def _role_codename_by_id(role_id: int | None) -> str | None:
    """The codename of a role, or None when the role is unresolved."""
    if role_id is None:
        return None
    from ..tenancy.workspace_role import WorkspaceRole as Role

    role = await Role.objects.get(id=role_id)
    if role is None:
        return None
    codename = role.codename
    return str(codename)


def generate_invite_code() -> str:
    """Human-typable code: 4+4 uppercase alphanumerics, no ambiguous chars."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


class PlanLimitError(Exception):
    """A workspace tier has no room for what was asked for.

    Defined here and re-exported from `app.services.ai.scenario` so the managers
    — which sit below the service layer — and the services can raise the same
    type without importing each other.
    """


async def _member_limit_reason(tenant_id: int) -> str | None:
    """Why this workspace cannot take another member, or None."""
    from app.services.tenancy.limits import check_member_limit

    tenant = await tenants.get(id=tenant_id)
    if tenant is None:
        return None
    return await check_member_limit(tenant)


async def _channel_limit_reason(tenant_id: int) -> str | None:
    """Why this workspace cannot bind another chat, or None."""
    from app.services.tenancy.limits import check_channel_limit

    tenant = await tenants.get(id=tenant_id)
    if tenant is None:
        return None
    return await check_channel_limit(tenant)


class TenantManager(BaseManager["Tenant"]):
    def __init__(self):
        from ..tenancy import Tenant

        super().__init__(Tenant)

    async def get_by_slug(self, slug: str) -> Optional["Tenant"]:
        return await self.get(slug=slug.strip().lower())

    async def get_or_create_owner(self, slug: str = "owner") -> "Tenant":
        """Bootstrap workspace: the tenant that owns pre-existing (legacy) rows."""
        existing = await self.get_by_slug(slug)
        if existing is not None:
            return existing
        # `plan` is a tier, and this workspace used to be created as the
        # non-tier "owner". It is `business` rather than the default `pro`
        # because it is the operator's own workspace and is expected to hold
        # every legacy source: an operator hitting "maximum 20 sources" on a
        # fresh install would read it as a bug, not a plan.
        return await self.create(
            name="Owner workspace",
            slug=slug.strip().lower(),
            plan="business",
            daily_cost_limit=5.0,
            max_sources=100,
        )


class TenantUserManager(BaseManager["TenantUser"]):
    def __init__(self):
        from ..tenancy import TenantUser

        super().__init__(TenantUser)

    async def get_member(self, *, tenant_id: int, channel: str, external_user_id: str) -> Optional["TenantUser"]:
        row = await self.get(
            tenant_id=tenant_id,
            channel=channel,
            external_user_id=str(external_user_id),
            is_active=True,
        )
        return row

    async def add_member(
        self,
        *,
        tenant_id: int,
        channel: str,
        external_user_id: str,
        role_id: int | None = None,
        role: str | None = None,
    ) -> TenantUser | None:
        """Backward-compatible wrapper: accept either role_id or legacy role string.

        Bind a messenger identity (a chat participant) to a workspace.

        No tier check here, unlike `add_web_member`, and that asymmetry is
        deliberate: the team quota counts seats in the web console, not people
        who messaged the bot. The workspace owner talking to their own agent is
        the product, not an extra seat — charging for it would make the free
        tier unusable for the thing it exists for.
        """
        if role is not None and role_id is None:
            # Resolve legacy role string to role_id (owner->owner, member->viewer)
            from app.models import WorkspaceRole as Role

            codename = _resolve_role_codename(role)
            role_obj = await Role.objects.filter(codename=codename).first()
            role_id = role_obj.id if role_obj else None

        existing = await self.get(tenant_id=tenant_id, channel=channel, external_user_id=str(external_user_id))
        if existing is not None:
            if not existing.is_active or existing.role_id != role_id:
                return await self.update_by_id(existing.id, is_active=True, role_id=role_id)
            return existing

        return await self.create(
            tenant_id=tenant_id, channel=channel, external_user_id=str(external_user_id), role_id=role_id
        )

    async def add_web_member(self, *, tenant_id, user_id, role_id=None, role=None):
        """Serialize seat admission; an active membership never changes role here."""
        from ..tenancy.workspace_role import WorkspaceRole
        from ..tenancy import Tenant

        if role_id is None and role is not None:
            resolved = await WorkspaceRole.objects.get(codename=_resolve_role_codename(role))
            role_id = resolved.id if resolved else None
        async with self._get_owned_session() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if tenant is None:
                raise ValueError("Workspace unavailable")
            existing = (
                await session.execute(
                    select(self.model)
                    .where(self.model.tenant_id == tenant_id, self.model.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None and existing.is_active:
                return existing
            blocked = await _member_limit_reason(tenant_id)
            if blocked:
                raise PlanLimitError(blocked)
            if existing is not None:
                existing.is_active = True
                existing.role_id = role_id
                existing.ai_chat_enabled = False
                return existing
            return await self.create(
                session=session,
                tenant_id=tenant_id,
                channel=WEB_CHANNEL,
                external_user_id=str(user_id),
                user_id=user_id,
                role_id=role_id,
            )

    async def web_memberships(self, user_id: int) -> list["TenantUser"]:
        """Active web memberships in active workspaces only.

        This list is the access source for `/app` and `/api`: a deactivated
        workspace must not stay reachable through a membership row that is still
        active, matching the chat identity resolver and invite redemption.
        """
        from ..tenancy import Tenant, TenantUser

        active_tenants = select(Tenant.id).where(Tenant.is_active.is_(True))
        rows = await self.filter(
            TenantUser.tenant_id.in_(active_tenants), user_id=user_id, channel=WEB_CHANNEL, is_active=True
        )
        return sorted(rows, key=lambda m: m.id)

    async def member_grants(self, membership) -> frozenset:
        if membership is None or not membership.is_active or membership.role_id is None:
            return frozenset()
        from ..tenancy.workspace_role import WorkspaceRole

        role = await WorkspaceRole.objects.get(id=membership.role_id)
        return workspace_grants(role.codename) if role is not None else frozenset()

    async def _change_team(self, *, tenant_id, membership_id, role_id=None, disable=False, ai_chat_enabled=None):
        """Serialize every privilege change on the workspace row, then recheck authority."""
        from ..tenancy.workspace_role import WorkspaceRole
        from ..tenancy import Tenant
        from ..identity.user import User

        actor = get_current_user()
        if actor is None or current_tenant_id() != tenant_id or is_bypass():
            return False
        async with self._get_owned_session() as session:
            workspace = (
                await session.execute(
                    select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if workspace is None:
                return False
            fresh_user = (
                await session.execute(
                    select(User).where(User.id == actor.id, User.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if fresh_user is None:
                return False
            members = list(
                (
                    await session.execute(
                        select(self.model)
                        .where(self.model.tenant_id == tenant_id)
                        .options(selectinload(self.model.role))
                    )
                ).scalars()
            )
            caller = next(
                (m for m in members if m.user_id == actor.id and m.channel == WEB_CHANNEL and m.is_active), None
            )
            target = next((m for m in members if m.id == membership_id and m.is_active), None)
            if caller is None or caller.role is None or target is None:
                return False
            authority, existing = caller.role.codename, target.role.codename if target.role else None
            if role_id is not None and target.user_id == actor.id:
                return False
            new_role = (
                None
                if role_id is None
                else (
                    await session.execute(select(WorkspaceRole).where(WorkspaceRole.id == role_id))
                ).scalar_one_or_none()
            )
            if role_id is not None and new_role is None:
                return False
            desired = new_role.codename if new_role else existing
            if authority != "owner" and (
                authority != "admin" or existing in ("owner", "admin", None) or desired in ("owner", "admin")
            ):
                return False
            if disable and existing in ("owner", None):
                return False
            if new_role is not None and existing == "owner" and desired != "owner":
                owners = [
                    m
                    for m in members
                    if m.id != target.id
                    and m.is_active
                    and m.user_id is not None
                    and m.role is not None
                    and m.role.codename == "owner"
                ]
                active_owners = (
                    (
                        await session.execute(
                            select(User.id).where(User.id.in_([m.user_id for m in owners]), User.is_active.is_(True))
                        )
                    )
                    .scalars()
                    .all()
                )
                if not active_owners:
                    return False
            if new_role is not None:
                target.role_id = new_role.id
            if disable:
                target.is_active = False
            if ai_chat_enabled is not None:
                if existing in ("owner", None):
                    return False
                target.ai_chat_enabled = bool(ai_chat_enabled)
            logger.info(
                "workspace_membership_changed tenant_id=%s actor_id=%s membership_id=%s",
                tenant_id,
                actor.id,
                membership_id,
            )
            return True

    async def change_member_role(self, *, tenant_id, membership_id, role_id):
        return await self._change_team(tenant_id=tenant_id, membership_id=membership_id, role_id=role_id)

    async def set_ai_access(self, *, tenant_id, membership_id, enabled):
        return await self._change_team(tenant_id=tenant_id, membership_id=membership_id, ai_chat_enabled=enabled)

    async def disable_web_member(self, *, tenant_id: int, membership_id: int) -> bool:
        return await self._change_team(tenant_id=tenant_id, membership_id=membership_id, disable=True)

    async def web_memberships_for_tenant(self, tenant_id: int) -> list["TenantUser"]:
        """Active web members of a workspace (with a `user_id` bound)."""
        rows = await self.filter(tenant_id=tenant_id, channel=WEB_CHANNEL, is_active=True)
        return [m for m in rows if m.user_id is not None]


class TenantInviteManager(BaseManager["TenantInvite"]):
    def __init__(self):
        from ..tenancy import TenantInvite

        super().__init__(TenantInvite)

    async def issue(
        self,
        *,
        tenant_id: int,
        role: str = "viewer",
        max_uses: int = 1,
        expires_at: datetime | None = None,
    ) -> tuple["TenantInvite", str]:
        """Create an invitation; returns (row, plaintext code shown once)."""
        from ..tenancy.workspace_role import WorkspaceRole as Role

        codename = _resolve_role_codename(role)
        if codename is None or codename == "owner":
            raise ValueError("Invitations require a non-owner workspace role")
        if not is_bypass():
            actor = get_current_user()
            if actor is None or current_tenant_id() != tenant_id:
                raise PermissionError("Workspace invitation denied")
            member = await tenant_users.prefetch_related("role").get(
                tenant_id=tenant_id, user_id=actor.id, channel=WEB_CHANNEL, is_active=True
            )
            authority = member.role.codename if member and member.role else None
            if authority != "owner" and (authority != "admin" or codename == "admin"):
                raise PermissionError("Workspace invitation denied")
        code = generate_invite_code()

        # Resolve role string → role_id
        codename = _resolve_role_codename(role)
        role_id: int | None = None
        if codename is not None:
            r = await Role.objects.filter(codename=codename).first()
            if r is not None:
                role_id = r.id

        row = await self.create(
            tenant_id=tenant_id,
            code_hash=hash_invite_code(code),
            role_id=role_id,
            max_uses=max_uses,
            expires_at=expires_at,
        )
        return row, code

    async def open_for_tenant(self, tenant_id: int) -> list["TenantInvite"]:
        """Invites of a workspace that can still be redeemed, newest first."""
        now = datetime.now(timezone.utc)
        rows = await self.filter(tenant_id=tenant_id, is_active=True)
        open_rows = [
            row for row in rows if (row.expires_at is None or row.expires_at > now) and row.used_count < row.max_uses
        ]
        return sorted(open_rows, key=lambda row: row.id, reverse=True)

    async def revoke(self, *, tenant_id: int, invite_id: int) -> bool:
        """Switch off one invite of this workspace; False when there is none to revoke.

        Bound to the workspace in the same statement, so an id from another
        workspace revokes nothing.
        """
        changed = await self.filter(id=invite_id, tenant_id=tenant_id, is_active=True).update(is_active=False)
        return bool(changed)

    async def _valid_invite(self, code: str) -> Optional["TenantInvite"]:
        invite = await self.get(code_hash=hash_invite_code(code), is_active=True)
        if invite is None:
            return None
        now = datetime.now(timezone.utc)
        if invite.expires_at is not None and invite.expires_at <= now:
            return None
        if invite.used_count >= invite.max_uses:
            return None
        tenant = await TenantManager().get(id=invite.tenant_id, is_active=True)
        if tenant is None:
            return None
        return invite

    async def redeem(self, *, code: str, channel: str, chat_id: str, external_user_id: str) -> dict[str, Any]:
        """Bind a chat+user to a tenant. Idempotent per (channel, chat_id)."""
        invite = await self._valid_invite(code)
        if invite is None or invite.role_id is None:
            return {"status": "invalid", "reason": "unknown, expired or exhausted code"}

        bound = await TenantChannelManager().get(channel=channel, chat_id=str(chat_id))
        if bound is not None:
            if bound.tenant_id == invite.tenant_id:
                return {"status": "already", "tenant_id": invite.tenant_id}
            return {"status": "conflict", "reason": "chat is already bound to another tenant"}

        await TenantUserManager().add_member(
            tenant_id=invite.tenant_id,
            channel=channel,
            external_user_id=external_user_id,
            role_id=invite.role_id,
        )
        await TenantChannelManager().bind(
            tenant_id=invite.tenant_id, channel=channel, chat_id=str(chat_id), kind="private"
        )
        await self.update_by_id(invite.id, used_count=invite.used_count + 1)
        return {
            "status": "bound",
            "tenant_id": invite.tenant_id,
            "role_id": invite.role_id,
            "role_codename": await _role_codename_by_id(invite.role_id),
        }

    async def redeem_web(self, *, code: str, user_id: int) -> dict[str, Any]:
        """Atomically consume a live invitation and admit one workspace seat."""
        from ..tenancy.workspace_role import WorkspaceRole
        from ..tenancy import Tenant, TenantUser
        from ..identity.user import User

        candidate = await self.get(code_hash=hash_invite_code(code))
        invalid = {"status": "invalid", "reason": "unknown, expired or exhausted code"}
        if candidate is None:
            return invalid
        async with self._get_owned_session() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(Tenant.id == candidate.tenant_id, Tenant.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if tenant is None:
                return invalid
            invite = (
                await session.execute(
                    select(self.model)
                    .where(self.model.id == candidate.id, self.model.is_active.is_(True))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                invite is None
                or invite.role_id is None
                or invite.used_count >= invite.max_uses
                or (invite.expires_at is not None and invite.expires_at <= datetime.now(timezone.utc))
            ):
                return invalid
            role = (
                await session.execute(select(WorkspaceRole).where(WorkspaceRole.id == invite.role_id))
            ).scalar_one_or_none()
            if role is None or role.codename not in {"admin", "manager", "analyst", "viewer"}:
                return invalid
            user = (
                await session.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
            ).scalar_one_or_none()
            if user is None:
                return invalid
            member = (
                await session.execute(
                    select(TenantUser)
                    .where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if member is not None and member.is_active:
                return {"status": "already", "tenant_id": tenant.id}
            blocked = await _member_limit_reason(tenant.id)
            if blocked:
                return {"status": "no_seat", "reason": blocked}
            if member is None:
                await TenantUserManager().create(
                    session=session,
                    tenant_id=tenant.id,
                    user_id=user_id,
                    channel=WEB_CHANNEL,
                    external_user_id=str(user_id),
                    role_id=role.id,
                    ai_chat_enabled=False,
                )
            else:
                member.is_active = True
                member.role_id = role.id
                member.ai_chat_enabled = False
            invite.used_count += 1
            return {"status": "bound", "tenant_id": tenant.id, "role_id": role.id}


class TenantChannelManager(BaseManager["TenantChannel"]):
    def __init__(self):
        from ..tenancy import TenantChannel

        super().__init__(TenantChannel)

    async def get_binding(self, *, channel: str, chat_id: str) -> Optional["TenantChannel"]:
        return await self.get(channel=channel, chat_id=str(chat_id), is_active=True)

    async def bind(self, *, tenant_id: int, channel: str, chat_id: str, kind: str = "private") -> TenantChannel | None:
        existing = await self.get(channel=channel, chat_id=str(chat_id))
        if existing is not None:
            if existing.tenant_id != tenant_id:
                raise ValueError("chat is already bound to another tenant")
            if not existing.is_active or existing.kind != kind:
                return await self.update_by_id(existing.id, is_active=True, kind=kind)
            return existing

        # Only a genuinely new binding consumes quota. Re-binding a chat this
        # workspace already owns (onboarding, invite redemption) returns above.
        blocked = await _channel_limit_reason(tenant_id)
        if blocked:
            raise PlanLimitError(blocked)

        return await self.create(tenant_id=tenant_id, channel=channel, chat_id=str(chat_id), kind=kind)

    async def digest_targets(self, tenant_id: int) -> list["TenantChannel"]:
        return await self.filter(tenant_id=tenant_id, is_digest_target=True, is_active=True)


tenants = TenantManager()
tenant_users = TenantUserManager()
tenant_invites = TenantInviteManager()
tenant_channels = TenantChannelManager()
