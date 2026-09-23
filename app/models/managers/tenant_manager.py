"""Tenant managers: invites, channel binding, credentials.

Invite codes are the only write path that crosses tenant boundaries: redeeming
a code creates a TenantUser + TenantChannel row for the caller's chat. Codes
are stored as SHA-256 hashes, checked for expiry/use-count, and the use count
is bumped atomically-ish (single row update; double-redeem of a 1-use code is
caught by the max_uses check on the second call).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..tenant import Tenant, TenantChannel, TenantCredential, TenantInvite, TenantUser


def hash_invite_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def generate_invite_code() -> str:
    """Human-typable code: 4+4 uppercase alphanumerics, no ambiguous chars."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


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
        return await self.create(
            name="Owner workspace",
            slug=slug.strip().lower(),
            plan="owner",
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
        self, *, tenant_id: int, channel: str, external_user_id: str, role: str = "owner"
    ) -> TenantUser | None:
        existing = await self.get(tenant_id=tenant_id, channel=channel, external_user_id=str(external_user_id))
        if existing is not None:
            if not existing.is_active or existing.role != role:
                return await self.update_by_id(existing.id, is_active=True, role=role)
            return existing
        return await self.create(
            tenant_id=tenant_id, channel=channel, external_user_id=str(external_user_id), role=role
        )


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
        code = generate_invite_code()
        row = await self.create(
            tenant_id=tenant_id,
            code_hash=hash_invite_code(code),
            role=role,
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
            role=invite.role,
        )
        await TenantChannelManager().bind(
            tenant_id=invite.tenant_id, channel=channel, chat_id=str(chat_id), kind="private"
        )
        await self.update_by_id(invite.id, used_count=invite.used_count + 1)
        return {"status": "bound", "tenant_id": invite.tenant_id, "role": invite.role}


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
        return await self.create(tenant_id=tenant_id, channel=channel, chat_id=str(chat_id), kind=kind)

    async def digest_targets(self, tenant_id: int) -> list["TenantChannel"]:
        return await self.filter(tenant_id=tenant_id, is_digest_target=True, is_active=True)


class TenantCredentialManager(BaseManager["TenantCredential"]):
    def __init__(self):
        from ..tenant import TenantCredential

        super().__init__(TenantCredential)

    async def store(
        self, *, tenant_id: int, platform: str, kind: str, secret: str, label: str | None = None
    ) -> "TenantCredential":
        from app.utils.crypto import encrypt_secret

        return await self.create(
            tenant_id=tenant_id,
            platform=platform,
            kind=kind,
            label=label,
            secret_encrypted=encrypt_secret(secret),
        )

    async def active(self, *, tenant_id: int, platform: str) -> list["TenantCredential"]:
        return await self.filter(tenant_id=tenant_id, platform=platform, is_active=True)


tenants = TenantManager()
tenant_users = TenantUserManager()
tenant_invites = TenantInviteManager()
tenant_channels = TenantChannelManager()
tenant_credentials = TenantCredentialManager()
