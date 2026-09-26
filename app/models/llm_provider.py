from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, Column, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TimestampMixin


@app_label("ai")
class LLMProvider(Base, TimestampMixin):
    __tablename__ = "llm_providers"
    __table_args__ = {"schema": settings.DB_SCHEMA}

    id: Mapped[int] = Column(Integer, primary_key=True)
    name: Mapped[str] = Column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = Column(Text, nullable=True)

    api_format: Mapped[str] = Column(String(20), nullable=False, default="openai")
    base_url: Mapped[str] = Column(String(500), nullable=False)
    auth_header: Mapped[str | None] = Column(String(200), nullable=True)
    encrypted_api_key: Mapped[str | None] = Column(Text, nullable=True)

    is_active: Mapped[bool] = Column(Boolean, default=True)
    is_default: Mapped[bool] = Column(Boolean, default=False)

    models = relationship(
        "LLMModel",
        back_populates="provider",
        cascade="all, delete-orphan",
        foreign_keys="LLMModel.provider_id",
    )

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager
        from .managers.llm_provider_manager import LLMProviderManager

        objects: ClassVar[LLMProviderManager | BaseManager]
    else:
        objects: ClassVar[Any] = None

    def __str__(self) -> str:
        return f"{self.name} [{self.api_format}]"

    def get_api_key(self) -> str:
        if self.encrypted_api_key:
            from ..utils.crypto import decrypt_secret

            return decrypt_secret(self.encrypted_api_key)
        return ""

    def decrypted_key_masked(self) -> str:
        key = self.get_api_key()
        if not key:
            return ""
        return key[:4] + "…" + key[-4:] if len(key) > 10 else "***"


from .managers.llm_provider_manager import LLMProviderManager  # noqa: E402

LLMProvider.objects = LLMProviderManager()
