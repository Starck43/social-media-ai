"""Query history for read-only 1C extraction: metadata, encrypted question, typed plan."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin


class OneCQueryHistory(Base, TenantScopedMixin, TimestampMixin):
    """One planned or executed 1C read-only query; never stores returned rows."""

    __tablename__ = "onec_query_history"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_onec_query_history_tenant"),
        CheckConstraint(
            "status IN ('executed','exported')",
            name="ck_onec_query_history_status",
        ),
        CheckConstraint(
            "exported_format IS NULL OR exported_format IN ('xlsx','pdf')",
            name="ck_onec_query_history_export_format",
        ),
        CheckConstraint("row_count >= 0", name="ck_onec_query_history_row_count"),
        CheckConstraint("duration_ms >= 0", name="ck_onec_query_history_duration"),
        Index("ix_onec_query_history_actor", "tenant_id", "user_id", "created_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[int] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.workspace_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_set: Mapped[str] = Column(String(128), nullable=False)
    plan_json: Mapped[dict] = Column(JSON, nullable=False)
    plan_fingerprint: Mapped[str] = Column(String(64), nullable=False)
    question_encrypted: Mapped[str | None] = Column(Text, nullable=True)
    row_count: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    duration_ms: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = Column(String(20), nullable=False)
    exported_format: Mapped[str | None] = Column(String(10), nullable=True)
    exported_at: Mapped[object | None] = Column(DateTime(timezone=True), nullable=True)
