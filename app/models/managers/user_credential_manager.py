"""Manager for the personal (user-scoped) credential vault.

See `app/models/user_credential.py` for the security model: this table is NOT
tenant-scoped, so every method takes `user_id` (and where relevant `platform`)
explicitly. Nothing here should ever be called with a tenant id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..user_credential import UserCredential


class UserCredentialManager(BaseManager["UserCredential"]):
    def __init__(self):
        from ..user_credential import UserCredential

        super().__init__(UserCredential)

    async def store(
        self, *, user_id: int, platform: str, kind: str, secret: str, label: str | None = None
    ) -> "UserCredential":
        from app.utils.crypto import encrypt_secret

        return await self.create(
            user_id=user_id,
            platform=platform,
            kind=kind,
            label=label,
            secret_encrypted=encrypt_secret(secret),
        )

    async def active(self, *, user_id: int, platform: str) -> list["UserCredential"]:
        return await self.filter(user_id=user_id, platform=platform, is_active=True)

    async def newest(self, *, user_id: int, platform: str, kind: str) -> Optional["UserCredential"]:
        """Newest active row of one kind for a user, or None."""
        rows = await self.active(user_id=user_id, platform=platform)
        candidates = [row for row in rows if row.kind == kind]
        candidates.sort(key=lambda row: row.updated_at or row.created_at, reverse=True)
        return candidates[0] if candidates else None


user_credentials = UserCredentialManager()
