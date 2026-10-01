from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
	JSON,
	Boolean,
	DateTime,
	ForeignKey,
	Index,
	Integer,
	String,
	UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from ..core.config import settings
from ..core.decorators import app_label
from ..types import SourceType
from .base import Base, TenantScopedMixin, TimestampMixin

if TYPE_CHECKING:
	from . import AIAnalytics, AgentScenario, Platform, Tenant
	from .managers.source_manager import SourceManager


@app_label("social")
class Source(Base, TenantScopedMixin, TimestampMixin):
	__tablename__ = "sources"

	__table_args__ = (
		UniqueConstraint(
			"tenant_id",
			"platform_id",
			"external_id",
			name="uq_source_tenant_platform_external",
		),
		Index("ix_sources_tenant_id", "tenant_id"),
		Index("idx_sources_platform_id", "platform_id"),
		Index("idx_sources_external_id", "external_id"),
		Index("idx_sources_last_checked", "last_checked"),
		{"schema": settings.DB_SCHEMA},
	)

	id: Mapped[int] = mapped_column(primary_key=True)  # Тип выводится автоматически
	platform_id: Mapped[int] = mapped_column(
		ForeignKey(f"{settings.DB_SCHEMA}.platforms.id", ondelete="CASCADE"),
		nullable=False,
	)
	name: Mapped[str] = mapped_column(String(255), nullable=False)  # String(255) нужен для длины
	source_type: Mapped[SourceType] = SourceType.sa_column(
		type_name="source_type", nullable=False, store_as_name=True
	)
	external_id: Mapped[str] = mapped_column(String(100), nullable=False)  # String(100) для длины
	params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
	is_active: Mapped[bool] = mapped_column(Boolean, default=True)
	last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
	# High-water mark of the newest ingested item for push-based sources
	# (Telegram Bot API): lets ingest skip replayed updates after a restart.
	last_item_id: Mapped[str | None] = mapped_column(
		String(100), nullable=True,
		comment="Watermark of the newest ingested item for push-based sources (Telegram Bot API)",
	)

	# Time range for data collection (optional boundaries)
	date_from: Mapped[datetime | None] = mapped_column(
		DateTime(timezone=True),
		nullable=True,
		comment="Start date for data collection (inclusive)",
	)
	date_to: Mapped[datetime | None] = mapped_column(
		DateTime(timezone=True),
		nullable=True,
		comment="End date for data collection (inclusive)",
	)

	# Relationships
	platform: Mapped["Platform"] = relationship("Platform", back_populates="sources")

	# Owning workspace (tenant)
	tenant: Mapped["Tenant"] = relationship("Tenant")

	# Assign reusable scenario per source
	agent_scenario_id: Mapped[int | None] = mapped_column(
		ForeignKey(f"{settings.DB_SCHEMA}.agent_scenarios.id", ondelete="SET NULL"),
		nullable=True,
	)
	# Link to reusable agent scenario; scenario is preserved on source deletion
	agent_scenario: Mapped["AgentScenario | None"] = relationship(
		"AgentScenario",
		back_populates="sources",
	)
	# Reverse relation for analytics entries created for this source
	analytics: Mapped[list["AIAnalytics"]] = relationship(
		"AIAnalytics",
		back_populates="source",
		cascade="all, delete-orphan",
		passive_deletes=True,
	)

	# Manager will be set after class definition
	if TYPE_CHECKING:
		objects: ClassVar[SourceManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		return f"{self.name} ({self.external_id})"

	@staticmethod
	def _clean_external_id(external_id: str) -> str:
		"""Extract username/ID from URL."""
		if not external_id:
			return external_id

		# Remove everything before the last slash
		return external_id.rstrip("/").split("/")[-1]

	@validates("external_id")
	def validate_external_id(self, _: str, external_id: str) -> str:
		"""Clean up external_id before validation."""
		return self._clean_external_id(external_id)

	@property
	def platform_url(self) -> str:
		return f"{self.platform.base_url}/{self.external_id}"


from .managers.source_manager import SourceManager  # noqa: E402

Source.objects = SourceManager()
