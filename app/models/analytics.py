from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, DateTime, ForeignKey, JSON, String, Float
from sqlalchemy.orm import relationship, Mapped

from .base import Base
from ..core.decorators import app_label

if TYPE_CHECKING:
	from . import SocialGroup


@app_label("dashboard")
class Statistics(Base):
	__tablename__ = 'statistics'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	group_id: Mapped[int] = Column(Integer, ForeignKey('social_manager.social_groups.id'))
	date: Mapped[DateTime] = Column(DateTime, nullable=False)
	metrics: Mapped[JSON] = Column(JSON, nullable=False)

	group: Mapped['SocialGroup'] = relationship("SocialGroup", back_populates="statistics")

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.analytics_manager import StatisticsManager
		objects: ClassVar[StatisticsManager | BaseManager]
	else:
		objects: ClassVar = None

	class Meta:
		app_label = "analytic"


@app_label("dashboard")
class AIAnalysisResult(Base):
	__tablename__ = 'ai_analysis_results'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	group_id: Mapped[int] = Column(Integer, ForeignKey('social_manager.social_groups.id'))
	analysis_type: Mapped[str] = Column(String(50), nullable=False)  # 'sentiment', 'topics', 'trends'
	period_start: Mapped[DateTime] = Column(DateTime, nullable=False)
	period_end: Mapped[DateTime] = Column(DateTime, nullable=False)
	results: Mapped[JSON] = Column(JSON, nullable=False)
	confidence_score: Mapped[float] = Column(Float)

	group: Mapped['SocialGroup'] = relationship("SocialGroup", back_populates="ai_analyses")

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.analytics_manager import AIAnalysisResultManager
		objects: ClassVar[AIAnalysisResultManager | BaseManager]
	else:
		objects: ClassVar = None


from .managers.analytics_manager import StatisticsManager, AIAnalysisResultManager # noqa: E402
Statistics.objects = StatisticsManager()
AIAnalysisResult.objects = AIAnalysisResultManager()
