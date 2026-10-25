"""Dedupe/new-item ledger for collection lanes.

A rejected item must not reappear as "new" on every run, but it is not
permanently excluded either: changing the task's filters re-evaluates it. The
ledger stores identity and the last outcome only; it never stores text.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager


@app_label("collection")
class CollectionSeen(Base, TenantScopedMixin, TimestampMixin):
    """Last known state of one platform identity in one task-owned source."""

    __tablename__ = "collection_seen"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "content_type",
            "external_id",
            name="uq_collection_seen_identity",
        ),
        Index("ix_collection_seen_tenant_source", "tenant_id", "source_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(String(20), nullable=False, default="post")
    # Writers synthesise `hash:<content_hash>` when the platform hands over no
    # id, so the identity stays non-null and the unique key is effective.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # `new`, `selected`, `needs_review` or `rejected`; a later run may replace it.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", server_default="new")

    if TYPE_CHECKING:
        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None
