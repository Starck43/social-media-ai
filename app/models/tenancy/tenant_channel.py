"""Workspace channel binding model; global chat uniqueness is unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager
    from ..managers.tenant_manager import TenantChannelManager


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
