from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, relationship

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin


@app_label("llm")
class LLMProvider(Base, TimestampMixin):
    """One provider row inside exactly one catalog.

    Custody is `catalog_id`: the platform catalog has no owner, a personal
    catalog names its user. `owner_user_id` is a **retained compatibility**
    column — the personal-catalog readers of increment A (and `P21-T12`'s
    `app/services/ai/**`) still filter by it — and the database guard
    `guard_llm_catalog_custody` keeps it a projection of the catalog, never a
    second authority: equal to the personal owner, NULL for platform rows.

    Credential custody follows the same kind: a platform provider may hold only
    an allowlisted deployment secret alias (`managed_credential_ref`) and never
    personal ciphertext; a personal provider holds only encrypted BYOK.
    """

    __tablename__ = "llm_providers"
    __personal_scoped__ = True
    __table_args__ = (
        UniqueConstraint("id", "catalog_id", name="uq_llm_provider_id_catalog"),
        UniqueConstraint("catalog_id", "uid", name="uq_llm_provider_catalog_uid"),
        ForeignKeyConstraint(
            ["catalog_id"],
            [f"{settings.DB_SCHEMA}.llm_catalogs.id"],
            name="fk_llm_provider_catalog",
            ondelete="RESTRICT",
        ),
        CheckConstraint("credential_version >= 1", name="ck_llm_provider_credential_version"),
        CheckConstraint(
            "managed_credential_ref IS NULL OR managed_credential_ref ~ '^[a-z][a-z0-9_-]{0,99}$'",
            name="ck_llm_provider_managed_ref_ctrl",
        ),
        Index("ix_llm_provider_catalog_id", "catalog_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    catalog_id: Mapped[int] = Column(Integer, nullable=False)
    # Retained compatibility owner: personal catalog owner, or NULL for platform.
    owner_user_id: Mapped[int | None] = Column(
        ForeignKey(
            f"{settings.DB_SCHEMA}.users.id",
            ondelete="RESTRICT",
            name="fk_llm_provider_owner_user",
        ),
        nullable=True,
        index=True,
    )
    uid: Mapped[str] = Column(String(100), nullable=False)
    name: Mapped[str] = Column(String(255), nullable=False)
    description: Mapped[str | None] = Column(Text, nullable=True)

    api_format: Mapped[str] = Column(String(20), nullable=False, default="openai")
    base_url: Mapped[str] = Column(String(500), nullable=False)
    auth_header: Mapped[str | None] = Column(String(200), nullable=True)
    encrypted_api_key: Mapped[str | None] = Column(Text, nullable=True)
    managed_credential_ref: Mapped[str | None] = Column(
        String(100), nullable=True, comment="Opaque deployment secret alias for platform catalogs only"
    )
    credential_version: Mapped[int] = Column(BigInteger, nullable=False, default=1, server_default="1")

    is_active: Mapped[bool] = Column(Boolean, default=True)

    # `catalog_id` is the custody FK and `LLMCatalog.providers` is the reverse
    # reader; neither is a `relationship()` yet, so this module configures on its
    # own (source-isolated loaders, the personal-catalog unit registry) without
    # an `LLMCatalog`/`AgentScenario` class being present. The join below is
    # spelled out for the same reason; the composite `fk_llm_model_provider_catalog`
    # still defines the columns.
    models = relationship(
        "LLMModel",
        back_populates="provider",
        cascade="all, delete-orphan",
        foreign_keys="[LLMModel.provider_id, LLMModel.catalog_id]",
        primaryjoin="and_(LLMProvider.id == foreign(LLMModel.provider_id), "
        "LLMProvider.catalog_id == foreign(LLMModel.catalog_id))",
    )

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.llm_provider_manager import LLMProviderManager

        objects: ClassVar[LLMProviderManager | BaseManager]
    else:
        objects: ClassVar[Any] = None

    def __str__(self) -> str:
        return f"{self.name} [{self.api_format}]"

    @property
    def is_platform_catalog(self) -> bool:
        """Whether custody is the platform catalog (owner-free, managed refs)."""
        return self.owner_user_id is None

    def get_api_key(self) -> str:
        """The personal BYOK secret; platform rows are not readable here.

        Managed credentials are resolved inside the platform dispatch path, not
        by this personal-era accessor, so a platform row never yields a value
        to a personal catalog reader.
        """
        from app.core.personal_catalog_context import current_catalog_owner_id

        if self.owner_user_id is None or self.owner_user_id != current_catalog_owner_id():
            raise PermissionError("Provider credentials belong to another user")
        if self.encrypted_api_key:
            from ...utils.crypto import decrypt_secret

            return decrypt_secret(self.encrypted_api_key)
        return ""

    def decrypted_key_masked(self) -> str:
        # Display never needs to decrypt, nor expose fragments of the key.
        return "***" if self.encrypted_api_key or self.managed_credential_ref else ""
