from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Any

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label
from ..types.models import SourceType

if TYPE_CHECKING:
	from . import Platform, AIAnalytics


@app_label("social")
class Source(Base, TimestampMixin):
	__tablename__ = 'sources'
	__table_args__ = (
		UniqueConstraint('platform_id', 'external_id', name='uq_source_platform_external'),
		Index('idx_sources_platform_id', 'platform_id'),
		Index('idx_sources_external_id', 'external_id'),
		Index('idx_sources_last_checked', 'last_checked'),
		{'schema': settings.DB_SCHEMA}
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	platform_id: Mapped[int] = mapped_column(
		Integer,
		ForeignKey("social_manager.platforms.id", ondelete="CASCADE"),
		nullable=False
	)
	name: Mapped[str] = mapped_column(String(255), nullable=False)
	source_type: Mapped[SourceType] = SourceType.sa_column(
		type_name='source_type',
		nullable=False,
		store_as_name=True
	)
	external_id: Mapped[str] = mapped_column(String(100), nullable=False)
	params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
	is_active: Mapped[bool] = mapped_column(Boolean, default=True)
	last_checked: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)

	# Relationships
	platform: Mapped["Platform"] = relationship("Platform", back_populates="sources")

	# Many-to-many self relation: sources can track specific user-typed sources
	monitored_users: Mapped[list["Source"]] = relationship(
		"Source",
		secondary="social_manager.source_user_relationships",
		primaryjoin="Source.id == SourceUserRelationship.source_id",
		secondaryjoin="Source.id == SourceUserRelationship.user_id",
		backref="tracked_in_sources",
		cascade="all, delete",
		passive_deletes=True
	)
	# Assign reusable scenario per source
	bot_scenario_id: Mapped[int | None] = mapped_column(
		Integer,
		ForeignKey("social_manager.bot_scenarios.id", ondelete="SET NULL"),
		nullable=True
	)
	# Link to reusable bot scenario; scenario is preserved on source deletion
	bot_scenario: Mapped["BotScenario | None"] = relationship(
		"BotScenario",
		back_populates="sources",
	)
	# Reverse relation for analytics entries created for this source
	analytics: Mapped[list["AIAnalytics"]] = relationship(
		"AIAnalytics",
		back_populates="source",
		cascade="all, delete-orphan",
		passive_deletes=True
	)

	# Manager will be set after class definition
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.source_manager import SourceManager
		objects: ClassVar[SourceManager | BaseManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		return f"{self.name} ({self.external_id})"

	@property
	def platform_url(self) -> str:
		return f"{self.platform.base_url}/{self.external_id}"


from .managers.source_manager import SourceManager  # noqa: E402
Source.objects = SourceManager()


class SourceUserRelationship(Base):
	__tablename__ = 'source_user_relationships'
	__table_args__ = {'schema': settings.DB_SCHEMA}

	source_id: Mapped[int] = Column(
		Integer,
		ForeignKey("social_manager.sources.id", ondelete='CASCADE'),
		primary_key=True
	)
	user_id: Mapped[int] = Column(
		Integer,
		ForeignKey("social_manager.sources.id", ondelete='CASCADE'),
		primary_key=True
	)

	# Relationships to related Source rows for admin display
	source: Mapped["Source"] = relationship(
		"Source",
		foreign_keys="SourceUserRelationship.source_id",
		lazy="select",
		overlaps="monitored_users,tracked_in_sources"
	)

	user: Mapped["Source"] = relationship(
		"Source",
		foreign_keys="SourceUserRelationship.user_id",
		lazy="select",
		overlaps="monitored_users,tracked_in_sources"
	)

	# Manager
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		objects: ClassVar[BaseManager]
	else:
		objects: ClassVar = None


from .managers.base_manager import BaseManager  # noqa: E402
SourceUserRelationship.objects = BaseManager()
