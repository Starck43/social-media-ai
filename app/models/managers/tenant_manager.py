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

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..tenant import Tenant, TenantChannel, TenantInvite, TenantUser

logger = logging.getLogger(__name__)


def hash_invite_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


WEB_CHANNEL = "web"


def _resolve_role_codename(role: str | None) -> str | None:
    """Map a legacy workspace-role string to a platform ``Role.codename``.

    Legacy strings "owner" → SUPERUSER, "member" → VIEWER.  If the string
    already matches a codename it is passed through unchanged.
    """
    if role is None:
        return None

    legacy_map = {"owner": "SUPERUSER", "member": "VIEWER"}
    return legacy_map.get(role, role)


async def _role_codename_by_id(role_id: int | None) -> str | None:
    """The codename of a role, or None when the role is unresolved."""
    if role_id is None:
        return None
    from ..role import Role

    role = await Role.objects.get(id=role_id)
    if role is None:
        return None
    codename = role.codename
    return codename.name if hasattr(codename, "name") else str(codename)


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
        from ..tenant import Tenant

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
        from ..tenant import TenantUser

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
        self, *, tenant_id: int, channel: str, external_user_id: str, role_id: int | None = None, role: str | None = None
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
            # Resolve legacy role string to role_id (owner->SUPERUSER, member->VIEWER)
            from app.models import Role
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

    async def add_web_member(self, *, tenant_id: int, user_id: int, role_id: int | None = None, role: str | None = None) -> "TenantUser | None":
        """Backward-compatible wrapper: accept either role_id or legacy role string.

        Web membership: same row, but bound to the `users` table via user_id.

        This is the path that consumes a team seat, so this is where the tier is
        enforced — which is also the path invite redemption takes.
        """
        if role is not None and role_id is None:
            # Resolve legacy role string to role_id (owner->SUPERUSER, member->VIEWER)
            from app.models import Role
            codename = _resolve_role_codename(role)
            role_obj = await Role.objects.filter(codename=codename).first()
            role_id = role_obj.id if role_obj else None

        existing = await self.get(tenant_id=tenant_id, user_id=user_id)
        if existing is not None:
            if not existing.is_active or existing.role_id != role_id:
                return await self.update_by_id(existing.id, is_active=True, role_id=role_id)
            return existing

        blocked = await _member_limit_reason(tenant_id)
        if blocked:
            raise PlanLimitError(blocked)

        return await self.create(
            tenant_id=tenant_id, channel=WEB_CHANNEL, external_user_id=str(user_id), user_id=user_id, role_id=role_id
        )

    async def web_memberships(self, user_id: int) -> list["TenantUser"]:
        rows = await self.filter(user_id=user_id, channel=WEB_CHANNEL, is_active=True)
        return sorted(rows, key=lambda m: m.id)

    async def web_memberships_for_tenant(self, tenant_id: int) -> list["TenantUser"]:
        """Active web members of a workspace (with a `user_id` bound)."""
        rows = await self.filter(tenant_id=tenant_id, channel=WEB_CHANNEL, is_active=True)
        return [m for m in rows if m.user_id is not None]


class TenantInviteManager(BaseManager["TenantInvite"]):
    def __init__(self):
        from ..tenant import TenantInvite

        super().__init__(TenantInvite)

    async def issue(
        self,
        *,
        tenant_id: int,
        role: str = "owner",
        max_uses: int = 1,
        expires_at: datetime | None = None,
    ) -> tuple["TenantInvite", str]:
        """Create an invitation; returns (row, plaintext code shown once)."""
        from app.core.database import async_session_maker
        from ..role import Role

        code = generate_invite_code()

        # Resolve role string → role_id
        codename = _resolve_role_codename(role)
        role_id: int | None = None
        if codename is not None:
            async with async_session_maker() as session:
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
        if invite is None:
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
        """Attach a logged-in web user to an invited tenant (no messenger chat).

        Idempotent per membership: an existing (even re-activated) member does
        not burn another use of the code.

        A workspace at its tier's team limit answers `no_seat` rather than
        raising: the invite is a *page* in the web flow, and turning a quota
        breach into an exception here would surface as a 500 on a form the
        person already filled in correctly.
        """
        invite = await self._valid_invite(code)
        if invite is None:
            return {"status": "invalid", "reason": "unknown, expired or exhausted code"}

        member = await TenantUserManager().get(tenant_id=invite.tenant_id, user_id=user_id)
        if member is not None and member.is_active:
            return {"status": "already", "tenant_id": invite.tenant_id}

        try:
            await TenantUserManager().add_web_member(tenant_id=invite.tenant_id, user_id=user_id, role_id=invite.role_id)
        except PlanLimitError as e:
            # The code is not burned: the seat may free up, and this person
            # should be able to redeem the same code once it does.
            logger.info("Invite for tenant %s refused by tier: %s", invite.tenant_id, e)
            return {"status": "no_seat", "reason": str(e)}

        await self.update_by_id(invite.id, used_count=invite.used_count + 1)
        return {"status": "bound", "tenant_id": invite.tenant_id, "role_id": invite.role_id}


class TenantChannelManager(BaseManager["TenantChannel"]):
    def __init__(self):
        from ..tenant import TenantChannel

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
