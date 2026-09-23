from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from .managers.digest_run_manager import DigestRunManager


@app_label("social")
class DigestRun(Base, TenantScopedMixin, TimestampMixin):
    """One digest build+delivery attempt (audit + idempotency per period)."""

    __tablename__ = "digest_runs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "period_start", "period_end", name="uq_digest_schedule_period"),
        Index("ix_digest_runs_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    schedule_id: Mapped[int | None] = Column(
        Integer,
        ForeignKey("social_manager.schedules.id", ondelete="SET NULL"),
        nullable=True,
    )
    period: Mapped[str] = Column(String(10), nullable=False)  # 'day' | 'week'
    period_start: Mapped[date] = Column(Date, nullable=False)
    period_end: Mapped[date] = Column(Date, nullable=False)
    channel: Mapped[str] = Column(String(20), nullable=False)  # 'telegram' | 'max' | 'auto'
    chat_id: Mapped[str] = Column(String(100), nullable=True)
    # 'pending' | 'sent' | 'failed' | 'skipped'
    status: Mapped[str] = Column(String(20), default="pending", nullable=False, server_default="pending")
    message_id: Mapped[str] = Column(String(100), nullable=True)
    content: Mapped[str] = Column(Text, nullable=True)
    error: Mapped[str] = Column(Text, nullable=True)

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager

        objects: ClassVar[DigestRunManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"DigestRun#{self.id}[{self.period} {self.period_start}..{self.period_end}] {self.status}"


from .managers.digest_run_manager import DigestRunManager  # noqa: E402

DigestRun.objects = DigestRunManager()
