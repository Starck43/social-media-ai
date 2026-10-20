"""Discovered people/communities and engagement actors.

A discovered result is a candidate, not a Source. The operator confirms it
(manually by default, or through a capped automatic rule) before it can become
a collection target. Evidence stays structured; raw provider bodies and
credentials never belong here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager


@app_label("collection")
class CollectionCandidate(Base, TenantScopedMixin, TimestampMixin):
    """One discovered identity awaiting confirmation or rejection."""

    __tablename__ = "collection_candidates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "platform",
            "kind",
            "external_id",
            name="uq_collection_candidates_identity",
        ),
        Index("ix_collection_candidates_tenant_status", "tenant_id", "status"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    # `person` or `community`; engagement actors are people.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    city_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # `new`, `confirmed` or `rejected`.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", server_default="new")
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    discovered_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    if TYPE_CHECKING:
        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None
