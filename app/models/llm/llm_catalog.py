"""Catalog custody: one platform catalog plus one personal catalog per user.

A provider row belongs to exactly one catalog, and the catalog — not the
compatibility `owner_user_id` columns retained on providers and models — is the
custody authority. Platform rows have no owner and never carry personal
ciphertext or a personal key; personal rows always name their owner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

PLATFORM_CATALOG_KIND = "platform"
PERSONAL_CATALOG_KIND = "personal"
CATALOG_KINDS: tuple[str, ...] = (PLATFORM_CATALOG_KIND, PERSONAL_CATALOG_KIND)


@app_label("llm")
class LLMCatalog(Base, TimestampMixin):
    """The custody scope of a provider/model fleet (P21-T13).

    `kind` and `owner_user_id` are a single fact split in two columns, so the
    CHECK below is the real constraint: a plain UNIQUE on a nullable owner
    would accept several platform catalogs, and a plain UNIQUE on `kind` would
    accept several personal catalogs, so both uniqueness rules are partial
    indexes created by the migration. Deactivation (`is_active`) is the
    ordinary retirement path; rows are never re-homed to another owner.
    """

    __tablename__ = "llm_catalogs"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'platform' AND owner_user_id IS NULL) OR (kind = 'personal' AND owner_user_id IS NOT NULL)",
            name="ck_llm_catalogs_kind_owner",
        ),
        Index(
            "uq_llm_catalogs_one_platform",
            "kind",
            unique=True,
            postgresql_where=(text("kind = 'platform'")),
        ),
        Index(
            "uq_llm_catalogs_one_personal_owner",
            "owner_user_id",
            unique=True,
            postgresql_where=(text("kind = 'personal'")),
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    kind: Mapped[str] = Column(String(16), nullable=False)
    owner_user_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT", name="fk_llm_catalogs_owner_user"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    # No `providers` relationship yet: providers are read through
    # `LLMProvider.objects.filter(catalog_id=...)` (the custody axis), and a
    # reverse relationship would make the provider module unloadable on its own.
    # It returns with the increment-B consumers that need eager loading.

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.llm_catalog_manager import LLMCatalogManager

        objects: ClassVar[LLMCatalogManager | BaseManager]
    else:
        objects: ClassVar[Any] = None

    @property
    def is_platform(self) -> bool:
        return self.kind == PLATFORM_CATALOG_KIND

    @property
    def is_personal(self) -> bool:
        return self.kind == PERSONAL_CATALOG_KIND

    def __str__(self) -> str:
        return f"{self.kind} catalog #{self.id}"
