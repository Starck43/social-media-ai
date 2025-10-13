from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, Date, ForeignKey, JSON, UniqueConstraint, Index, text, Enum, String, Text
from sqlalchemy.orm import Mapped, relationship

from .base import Base, TimestampMixin
from ..core.config import settings
from ..core.decorators import app_label
from ..types.models import PeriodType

if TYPE_CHECKING:
	from . import Source


@app_label("dashboard")
class AIAnalytics(Base, TimestampMixin):
	__tablename__ = 'ai_analytics'
	__table_args__ = (
		UniqueConstraint('source_id', 'analysis_date', 'period_type', name='uq_analytics_source_date_period'),
		Index('idx_ai_analytics_source', 'source_id'),
		Index('idx_ai_analytics_date', 'analysis_date'),
		Index('idx_ai_analytics_topic_chain', 'topic_chain_id'),
		{'schema': settings.DB_SCHEMA}
	)

	id: Mapped[int] = Column(Integer, primary_key=True)
	source_id: Mapped[int] = Column(
		Integer,
		ForeignKey('social_manager.sources.id', ondelete="CASCADE"),
		nullable=False
	)
	analysis_date: Mapped[Date] = Column(
		Date,
		nullable=True,
		default=date.today,
		server_default=text("CURRENT_DATE")
	)
	# Store as PostgreSQL enum matching existing DB type social_manager.analysis_period_type
	period_type: Mapped[PeriodType] = PeriodType.sa_column(
		type_name='analysis_period_type',
		nullable=False,
		default=PeriodType.DAILY,
		store_as_name=True
	)

	# Chain tracking for ongoing topics/threads
	topic_chain_id: Mapped[str] = Column(String(100), nullable=True)
	parent_analysis_id: Mapped[int] = Column(ForeignKey("social_manager.ai_analytics.id"), nullable=True)

	summary_data: Mapped[JSON] = Column(
		JSON,
		nullable=False,
		default=dict,
		server_default=text("'{}'::jsonb")
	)

	# Minimal LLM trace fields
	llm_model: Mapped[str | None] = Column(String(100), nullable=True)
	prompt_text: Mapped[str | None] = Column(Text, nullable=True)
	response_payload: Mapped[JSON] = Column(JSON, nullable=True)

	# Relationships
	source: Mapped["Source"] = relationship("Source", back_populates="analytics")
	parent: Mapped["AIAnalytics | None"] = relationship(
		"AIAnalytics",
		remote_side="AIAnalytics.id",
		back_populates="children",
		foreign_keys="AIAnalytics.parent_analysis_id"
	)
	children: Mapped[list["AIAnalytics"]] = relationship(
		"AIAnalytics",
		back_populates="parent",
		cascade="all, delete-orphan"
	)

	# Manager will be set after class definition
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.ai_analytics_manager import AIAnalyticsManager
		objects: ClassVar[AIAnalyticsManager | BaseManager]
	else:
		objects: ClassVar = None

	def __str__(self) -> str:
		_date = self.analysis_date
		date_str = _date.strftime('%d-%m-%Y') if _date and isinstance(_date, date) else ""

		return f"Аналитика по {self.source_id} за {date_str}"


from .managers.ai_analytics_manager import AIAnalyticsManager  # noqa: E402
AIAnalytics.objects = AIAnalyticsManager()
