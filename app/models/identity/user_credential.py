"""User-scoped third-party credentials (the personal vault).

`user_credentials` holds secrets that belong to a *person*: the L2 (user) tokens
that let the runtime act on behalf of a specific platform account (VK user_token,
Telegram MTProto session). Application/infrastructure secrets (VK app_id,
client_secret, service_token, bot tokens) are one-per-deployment config and are
read from the environment, not stored here. One person may own several
workspaces; their personal tokens live here once and are shared across those
workspaces — never duplicated per tenant.

Security model: the table is deliberately NOT tenant-scoped (like the `Tenant*`
tables) because a user's tokens are valid in every workspace they belong to.
Access control is enforced at the call site (`app.services.social.owner`): a
workspace may only use a token of a user who is an active member of that
workspace (see `tenant_users`), and `Source.params["token_owner"]` records which
user's token a source should use.

The secrets are encrypted at rest (Fernet, `CREDENTIALS_KEY`). Plaintext exists
only as the return value of `resolve_token` — never logged, never echoed into a
chat.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from .. import Base, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager
    from ..managers.user_credential_manager import UserCredentialManager


@app_label("account")
class UserCredential(Base, TimestampMixin):
    """A personal platform credential (L2 user token / session) owned by a user."""

    __tablename__ = "user_credentials"
    __table_args__ = (
        Index("ix_user_credentials_user_id", "user_id"),
        Index("ix_user_credentials_user_platform", "user_id", "platform"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = Column(String(30), nullable=False)  # 'vk' | 'telegram' | ...
    kind: Mapped[str] = Column(String(30), nullable=False)  # 'user_token' | 'session' | ...
    label: Mapped[str | None] = Column(String(100), nullable=True)
    secret_encrypted: Mapped[str] = Column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any] | None] = Column(JSON, nullable=True)
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    if TYPE_CHECKING:
        objects: ClassVar[UserCredentialManager | BaseManager]
    else:
        objects: ClassVar = None

    def reveal(self) -> str:
        """Decrypt the secret (call sparingly; never log the result)."""
        from app.utils.crypto import decrypt_secret

        return decrypt_secret(self.secret_encrypted)
