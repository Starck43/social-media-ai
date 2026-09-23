"""Multi-tenant core: tenants, membership, invite codes, channels, credentials.

One row here owns everything a client touches; all tenant-scoped tables point
at `tenants.id` and are filtered by `BaseManager` on that column.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from . import Base, TimestampMixin

if TYPE_CHECKING:
    from .managers.base_manager import BaseManager
    from .managers.tenant_manager import (
        TenantChannelManager,
        TenantCredentialManager,
        TenantInviteManager,
        TenantManager,
        TenantUserManager,
    )


@app_label("account")
class Tenant(Base, TimestampMixin):
    """A client workspace (or your own "owner" workspace)."""

    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenant_slug"),
        Index("ix_tenants_slug", "slug"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    name: Mapped[str] = Column(String(100), nullable=False)
    slug: Mapped[str] = Column(String(50), nullable=False)
    plan: Mapped[str] = Column(String(30), nullable=False, default="personal", server_default="personal")
    timezone: Mapped[str] = Column(String(50), nullable=False, default="Europe/Moscow", server_default="Europe/Moscow")
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")
    daily_cost_limit: Mapped[float] = Column(Float, nullable=False, default=5.0, server_default="5.0")
    max_sources: Mapped[int] = Column(Integer, nullable=False, default=20, server_default="20")

    if TYPE_CHECKING:
        objects: ClassVar[TenantManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


@app_label("account")
class TenantUser(Base, TimestampMixin):
    """Membership: a messenger identity belongs to a tenant with a role."""

    __tablename__ = "tenant_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "external_user_id", name="uq_tenant_user_identity"),
        Index("ix_tenant_users_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = Column(String(20), nullable=False)
    external_user_id: Mapped[str] = Column(String(100), nullable=False)
    role: Mapped[str] = Column(String(20), nullable=False, default="owner", server_default="owner")
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    if TYPE_CHECKING:
        objects: ClassVar[TenantUserManager | BaseManager]
    else:
        objects: ClassVar = None


@app_label("account")
class TenantInvite(Base, TimestampMixin):
    """Invite codes: the only way a new chat joins a tenant (`/start <code>`)."""

    __tablename__ = "tenant_invites"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_tenant_invite_code_hash"),
        Index("ix_tenant_invites_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = Column(String(64), nullable=False)
    role: Mapped[str] = Column(String(20), nullable=False, default="owner", server_default="owner")
    expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int] = Column(Integer, nullable=False, default=1, server_default="1")
    used_count: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    if TYPE_CHECKING:
        objects: ClassVar[TenantInviteManager | BaseManager]
    else:
        objects: ClassVar = None


@app_label("account")
class TenantChannel(Base, TimestampMixin):
    """A messenger chat bound to exactly one tenant (unique channel+chat_id)."""

    __tablename__ = "tenant_channels"
    __table_args__ = (
        UniqueConstraint("channel", "chat_id", name="uq_tenant_channel_chat"),
        Index("ix_tenant_channels_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = Column(String(20), nullable=False)  # 'telegram' | 'max'
    chat_id: Mapped[str] = Column(String(100), nullable=False)
    kind: Mapped[str] = Column(String(20), nullable=False, default="private", server_default="private")
    is_digest_target: Mapped[bool] = Column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    if TYPE_CHECKING:
        objects: ClassVar[TenantChannelManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"TenantChannel#{self.id}[{self.channel}:{self.chat_id}]"


@app_label("account")
class TenantCredential(Base, TimestampMixin):
    """Per-tenant third-party secrets, encrypted at rest (Fernet)."""

    __tablename__ = "tenant_credentials"
    __table_args__ = (
        Index("ix_tenant_credentials_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = Column(String(30), nullable=False)  # 'vk' | 'telegram' | 'llm' | ...
    kind: Mapped[str] = Column(String(30), nullable=False)  # 'user_token' | 'community_token' | 'api_key'
    label: Mapped[str | None] = Column(String(100), nullable=True)
    secret_encrypted: Mapped[str] = Column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any] | None] = Column(JSON, nullable=True)
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    if TYPE_CHECKING:
        objects: ClassVar[TenantCredentialManager | BaseManager]
    else:
        objects: ClassVar = None

    def reveal(self) -> str:
        """Decrypt the secret (call sparingly; never log the result)."""
        from app.utils.crypto import decrypt_secret

        return decrypt_secret(self.secret_encrypted)


from .managers.tenant_manager import (  # noqa: E402
    TenantChannelManager,
    TenantCredentialManager,
    TenantInviteManager,
    TenantManager,
    TenantUserManager,
)

Tenant.objects = TenantManager()
TenantUser.objects = TenantUserManager()
TenantInvite.objects = TenantInviteManager()
TenantChannel.objects = TenantChannelManager()
TenantCredential.objects = TenantCredentialManager()
