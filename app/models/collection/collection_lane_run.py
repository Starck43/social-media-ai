"""Per-run evidence for one collection lane.

A job may run several lanes. This table answers "what did the run actually
do?" without storing rejected text: fetched/selected/needs-review/rejected
counts, the effective bound, whether it was reached, and the cursor. Job and
task ids are logical references, not FKs, so pruning a job does not destroy
the evidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager


@app_label("collection")
class CollectionLaneRun(Base, TenantScopedMixin, TimestampMixin):
    """Counts and bounds of one typed collection lane in one job."""

    __tablename__ = "collection_lane_runs"
    __table_args__ = (
        Index("ix_collection_lane_runs_tenant_job", "tenant_id", "job_id"),
        Index("ix_collection_lane_runs_tenant_task", "tenant_id", "task_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lane_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. `wall_posts`, `post_search`, `comments`, `likes`.
    lane_type: Mapped[str] = mapped_column(String(30), nullable=False)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    needs_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # `limit` is a SQL keyword; keep the internal column unambiguous.
    limit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_reached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    next_cursor: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_available: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    unsupported_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    if TYPE_CHECKING:
        objects: ClassVar[BaseManager]
    else:
        objects: ClassVar = None
