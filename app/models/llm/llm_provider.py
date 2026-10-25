from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, relationship

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin


@app_label("llm")
class LLMProvider(Base, TimestampMixin):
    __tablename__ = "llm_providers"
    __personal_scoped__ = True
    __table_args__ = (
        UniqueConstraint("owner_user_id", "uid", name="uq_llm_provider_owner_uid"),
        UniqueConstraint("id", "owner_user_id", name="uq_llm_provider_id_owner"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = Column(
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    uid: Mapped[str] = Column(String(100), nullable=False)
    name: Mapped[str] = Column(String(255), nullable=False)
    description: Mapped[str | None] = Column(Text, nullable=True)

    api_format: Mapped[str] = Column(String(20), nullable=False, default="openai")
    base_url: Mapped[str] = Column(String(500), nullable=False)
    auth_header: Mapped[str | None] = Column(String(200), nullable=True)
    encrypted_api_key: Mapped[str | None] = Column(Text, nullable=True)

    is_active: Mapped[bool] = Column(Boolean, default=True)

    models = relationship(
        "LLMModel",
        back_populates="provider",
        cascade="all, delete-orphan",
        foreign_keys="[LLMModel.provider_id, LLMModel.owner_user_id]",
    )

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.llm_provider_manager import LLMProviderManager

        objects: ClassVar[LLMProviderManager | BaseManager]
    else:
        objects: ClassVar[Any] = None

    def __str__(self) -> str:
        return f"{self.name} [{self.api_format}]"

    def get_api_key(self) -> str:
        from app.core.personal_catalog_context import current_catalog_owner_id
        if self.owner_user_id != current_catalog_owner_id():
            raise PermissionError("Provider credentials belong to another user")
        if self.encrypted_api_key:
            from ...utils.crypto import decrypt_secret

            return decrypt_secret(self.encrypted_api_key)
        return ""

    def decrypted_key_masked(self) -> str:
        key = self.get_api_key()
        if not key:
            return ""
        return key[:4] + "…" + key[-4:] if len(key) > 10 else "***"
