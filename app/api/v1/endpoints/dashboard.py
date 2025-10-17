"""Dashboard API endpoints for statistics and summaries."""

import logging
from datetime import date, timedelta
from typing import Optional, List, TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Source, AIAnalytics, Platform, Notification
from app.core.database import get_db
from app.schemas.dashboard import (
	DashboardStats,
	SourceSummary,
	AnalyticsSummary,
	TrendData,
)
from app.services.ai.topic_chain_service import TopicChainService
from app.services.ai.reporting import ReportAggregator
from app.services.user.auth import get_authenticated_user
from app.types import SourceType, PeriodType

if TYPE_CHECKING:
	from app.models import User

router = APIRouter(tags=["dashboard"])
logger = logging.getLogger(__name__)


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
		platform_id: Optional[int] = None,
		source_type: Optional[SourceType] = None,
		since: Optional[date] = Query(None, description="Stats since this date"),
):
	"""
	Get dashboard statistics with optional filters.

	Returns comprehensive statistics about sources, platforms, analytics and notifications.
	"""

	# Get sources with filters
	source_query = Source.objects.filter()
	if platform_id:
		source_query = source_query.filter(platform_id=platform_id)
	if source_type:
		source_query = source_query.filter(source_type=source_type.name)

	sources = await source_query
	active_sources = [s for s in sources if s.is_active]

	# Get platforms
	platforms = await Platform.objects.filter()
	active_platforms = [p for p in platforms if p.is_active]

	# Get analytics with date filter
	analytics_query = AIAnalytics.objects.filter()
	if since:
		analytics_query = analytics_query.filter(analysis_date__gte=since)
	analytics = await analytics_query

	# Count unique topics from analytics
	unique_topics = set()
	for a in analytics:
		if hasattr(a, 'summary_data') and a.summary_data:
			ai_analysis = a.summary_data.get("ai_analysis", {})
			topic_analysis = ai_analysis.get("topic_analysis", {})
			topics = topic_analysis.get("main_topics", [])
			unique_topics.update(topics)

	# Get unread notifications
	unread_notifications = await Notification.objects.filter(is_read=False)

	# Count by platform
	sources_by_platform = {}
	for source in sources:
		platform_name = f"Platform {source.platform_id}"
		# Try to find platform name
		for p in platforms:
			if p.id == source.platform_id:
				platform_name = p.name
				break
		sources_by_platform[platform_name] = (
				sources_by_platform.get(platform_name, 0) + 1
		)

	# Count by source type
	sources_by_type = {}
	for source in sources:
		stype = str(source.source_type) if source.source_type else "unknown"
		sources_by_type[stype] = sources_by_type.get(stype, 0) + 1

	# Count analytics by period
	analytics_by_period = {}
	for analytic in analytics:
		period = str(analytic.period_type) if analytic.period_type else "unknown"
		analytics_by_period[period] = analytics_by_period.get(period, 0) + 1

	return DashboardStats(
		total_sources=len(sources),
		active_sources=len(active_sources),
		total_platforms=len(platforms),
		active_platforms=len(active_platforms),
		total_analytics=len(analytics),
		total_topics=len(unique_topics),
		unread_notifications=len(unread_notifications),
		sources_by_platform=sources_by_platform,
		sources_by_type=sources_by_type,
		analytics_by_period=analytics_by_period,
	)


@router.get("/sources", response_model=list[SourceSummary])
async def get_sources_summary(
		platform_id: Optional[int] = None,
		source_type: Optional[SourceType] = None,
		is_active: Optional[bool] = None,
		has_scenario: Optional[bool] = None,
		limit: int = Query(50, ge=1, le=100),
		offset: int = Query(0, ge=0),
):
	"""
	Get sources summary with filters.

	Filters:
	— platform_id: Filter by a platform
	— source_type: Filter by source type
	— is_active: Filter by active status
	— has_scenario: Filter sources with/without bot scenario
	— limit/offset: Pagination
	"""
	logger.info(
		f"Requesting sources summary (platform={platform_id}, type={source_type}, active={is_active})"
	)

	# Build query
	query = Source.objects.filter()

	if platform_id:
		query = query.filter(platform_id=platform_id)
	if source_type:
		query = query.filter(source_type=source_type.name)
	if is_active is not None:
		query = query.filter(is_active=is_active)

	sources = await query.order_by(Source.updated_at.desc()).offset(offset).limit(limit)

	# Get all platforms for name resolution
	platforms = await Platform.objects.filter()
	platform_map = {p.id: p.name for p in platforms}

	# Get analytics count per source
	all_analytics = await AIAnalytics.objects.filter()
	analytics_count = {}
	for a in all_analytics:
		analytics_count[a.source_id] = analytics_count.get(a.source_id, 0) + 1

	# Filter by scenario if needed
	if has_scenario is not None:
		sources = [
			s
			for s in sources
			if (s.bot_scenario_id is not None) == has_scenario
		]

	# Get bot scenarios
	from app.models import BotScenario

	scenarios = await BotScenario.objects.filter()
	scenario_map = {s.id: s.name for s in scenarios}

	result = []
	for source in sources:
		result.append(
			SourceSummary(
				id=source.id,
				name=source.name,
				platform_name=platform_map.get(source.platform_id, f"Platform {source.platform_id}"),
				source_type=str(source.source_type) if source.source_type else "unknown",
				is_active=source.is_active,
				last_checked=source.last_checked.isoformat()
				if source.last_checked
				else None,
				analytics_count=analytics_count.get(source.id, 0),
				bot_scenario_name=scenario_map.get(source.bot_scenario_id)
				if source.bot_scenario_id
				else None,
			)
		)

	return result


@router.get("/analytics", response_model=list[AnalyticsSummary])
async def get_analytics_summary(
		source_id: Optional[int] = None,
		period_type: Optional[PeriodType] = None,
		since: Optional[date] = Query(None, description="Show analytics since this date"),
		limit: int = Query(50, ge=1, le=100),
		offset: int = Query(0, ge=0),
		current_user: 'User' = Depends(get_authenticated_user),
):
	"""
	Get analytics summary with filters.

	Filters:
	— source_id: Filter by a source
	— period_type: Filter by period type
	— since: Show analytics created after this date
	— limit/offset: Pagination
	"""
	logger.info(
		f"User {current_user.username} requesting analytics summary "
		f"(source={source_id}, period={period_type}, since={since})"
	)

	# Build query
	query = AIAnalytics.objects.filter()

	if source_id:
		query = query.filter(source_id=source_id)
	if period_type:
		query = query.filter(period_type=period_type.value)
	if since:
		query = query.filter(analysis_date__gte=since)

	analytics = await (
		query.order_by(AIAnalytics.created_at.desc()).offset(offset).limit(limit)
	)

	# Get sources for name resolution
	sources = await Source.objects.filter()
	source_map = {s.id: s.name for s in sources}

	return [
		AnalyticsSummary(
			id=a.id,
			source_id=a.source_id,
			source_name=source_map.get(a.source_id, f"Source {a.source_id}"),
			analysis_date=a.analysis_date.isoformat() if a.analysis_date else "",
			period_type=str(a.period_type) if a.period_type else "unknown",
			topic_chain_id=a.topic_chain_id,
			llm_model=a.llm_model,
			created_at=a.created_at.isoformat() if a.created_at else "",
		)
		for a in analytics
	]


@router.get("/trends/{source_id}", response_model=list[TrendData])
async def get_source_trends(
		source_id: int,
		days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
		metric: str = Query("sentiment", description="Metric to track (sentiment, activity, engagement)"),
		current_user: 'User' = Depends(get_authenticated_user),
):
	"""
	Get trend data for a specific source.

	Tracks selected metric over time.
	"""
	logger.info(
		f"User {current_user.username} requesting trends for source {source_id} "
		f"(days={days}, metric={metric})"
	)

	# Verify source exists
	source = await Source.objects.get(id=source_id)
	if not source:
		raise HTTPException(status_code=404, detail="Source not found")

	# Get analytics for the period
	start_date = date.today() - timedelta(days=days)
	analytics = await AIAnalytics.objects.filter(
		source_id=source_id, analysis_date__gte=start_date
	).order_by(AIAnalytics.analysis_date.asc())

	if not analytics:
		logger.info(f"No analytics data found for source {source_id}")
		return []

	# Extract trend data based on metric
	trends = []
	for a in analytics:
		value = 0.0
		label = ""

		if a.summary_data:
			if metric == "sentiment":
				# Extract sentiment score
				ai_analysis = a.summary_data.get("ai_analysis", {})
				sentiment = ai_analysis.get("sentiment_analysis", {})
				value = sentiment.get("sentiment_score", 0.0)
				label = sentiment.get("overall_sentiment", "neutral")

			elif metric == "activity":
				# Extract activity metrics
				content_stats = a.summary_data.get("content_statistics", {})
				value = float(content_stats.get("total_posts", 0))
				label = f"{int(value)} posts"

			elif metric == "engagement":
				# Extract engagement rate
				content_stats = a.summary_data.get("content_statistics", {})
				value = content_stats.get("avg_reactions_per_post", 0.0)
				label = f"{value:.1f} avg reactions"

		trends.append(
			TrendData(
				date=a.analysis_date.isoformat() if a.analysis_date else "",
				value=value,
				label=label,
			)
		)

	return trends


@router.get("/notifications/recent", response_model=List)
async def get_recent_notifications(
		limit: int = Query(10, ge=1, le=50),
		current_user: 'User' = Depends(get_authenticated_user),
):
	"""Get recent notifications for dashboard display."""
	logger.info(f"User {current_user.username} requesting recent notifications")

	return await (
		Notification.objects.filter()
		.order_by(Notification.created_at.desc())
		.limit(limit)
	)


# Инициализация сервиса цепочек тем
topic_chain_service = TopicChainService()


@router.get("/topic-chains", response_model=list[dict])
async def get_topic_chains(
		source_id: Optional[int] = None,
		limit: int = Query(50, ge=1, le=200, description="Maximum number of chains to return"),
):
	"""
	Получить список всех цепочек тем.

	Returns:
		Список цепочек с базовой информацией
	"""

	# Получить все аналитики с topic_chain_id
	query = AIAnalytics.objects.filter()

	if source_id:
		query = query.filter(source_id=source_id)

	analytics = await query.limit(limit)

	# Группировка по цепочкам
	chains = {}
	source_ids = set()
	
	for a in analytics:
		chain_id = getattr(a, 'topic_chain_id', None)
		if chain_id:
			if chain_id not in chains:
				chains[chain_id] = {
					"chain_id": chain_id,
					"source_id": a.source_id,
					"analyses_count": 0,
					"first_date": None,
					"last_date": None,
					"topics_count": 0,
					"topics": []
				}
				source_ids.add(a.source_id)
			
			chains[chain_id]["analyses_count"] += 1
			
			# Обновить даты
			if not chains[chain_id]["first_date"] or a.analysis_date < chains[chain_id]["first_date"]:
				chains[chain_id]["first_date"] = a.analysis_date
			if not chains[chain_id]["last_date"] or a.analysis_date > chains[chain_id]["last_date"]:
				chains[chain_id]["last_date"] = a.analysis_date
			
			# Добавить темы из summary_data или response_payload аналитики
			all_chain_topics = set()

			try:
				# Извлечь темы из summary_data аналитики
				if hasattr(a, 'summary_data') and a.summary_data:
					summary = a.summary_data
					multi_llm = summary.get("multi_llm_analysis", {})

					text_analysis = multi_llm.get("text_analysis", {})
					main_topics = text_analysis.get("main_topics", [])

					# Создать объекты тем из строк
					sentiment = text_analysis.get("overall_mood", "neutral")
					for topic in main_topics:
						if topic:
							topic_obj = {
								"topic": topic,
								"prevalence": 0.8,  # Заглушка, можно рассчитать
								"analysis_type": "text",
								"sentiment": sentiment,
								"confidence": 0.8
							}
							all_chain_topics.add(frozenset(topic_obj.items()))

				# Извлечь темы из response_payload (для старых записей)
				if hasattr(a, 'response_payload') and a.response_payload:
					response = a.response_payload
					if "text_analysis" in response:
						parsed = response["text_analysis"].get("parsed", {})
						topic_analysis = parsed.get("topic_analysis", {})
						main_topics = topic_analysis.get("main_topics", [])

						sentiment = parsed.get("sentiment_analysis", {}).get("overall_sentiment", "neutral")
						for topic in main_topics:
							if topic:
								topic_obj = {
									"topic": topic,
									"prevalence": 0.8,
									"analysis_type": "text",
									"sentiment": sentiment,
									"confidence": 0.8
								}
								all_chain_topics.add(frozenset(topic_obj.items()))

			except Exception as e:
				logger.warning(f"Error extracting topics from analytics {a.id}: {e}")

			# Добавить извлеченные темы в цепочку
			for topic_frozenset in all_chain_topics:
				topic_dict = dict(topic_frozenset)
				if topic_dict not in chains[chain_id]["topics"]:
					chains[chain_id]["topics"].append(topic_dict)

	# Загрузить информацию об источниках
	sources_map = {}
	if source_ids:
		sources = await Source.objects.select_related(Source.platform).filter(Source.id.in_(list(source_ids)))
		for source in sources:
			sources_map[source.id] = {
				"id": source.id,
				"name": source.name,
				"platform": source.platform.name if source.platform else "unknown",
				"platform_type": source.platform.platform_type.db_value if source.platform else "unknown",
				"external_id": source.external_id,
				"base_url": source.platform.base_url if source.platform else ""
			}
	
	# Добавить информацию об источниках к цепочкам
	result = []
	for chain in chains.values():
		chain["source"] = sources_map.get(chain["source_id"])
		chain["topics_count"] = len(chain["topics"])

		# Добавить красивое название цепочки на основе топ-тем
		top_topics = []
		if chain.get("topics"):
			# Подсчет частоты тем
			topic_freq = {}
			for topic in chain["topics"]:
				# Тема может быть строкой или объектом
				if isinstance(topic, dict):
					topic_name = topic.get("topic", "")
				else:
					topic_name = str(topic)
				topic_freq[topic_name] = topic_freq.get(topic_name, 0) + 1

			# Взять топ-3 темы
			sorted_topics = sorted(topic_freq.items(), key=lambda x: x[1], reverse=True)
			top_topics = [topic[0] for topic in sorted_topics[:3]]

		# Сгенерировать название
		if len(top_topics) == 1:
			title = f"Тема: {top_topics[0]}"
		elif len(top_topics) == 2:
			title = f"Темы: {top_topics[0]} и {top_topics[1]}"
		elif len(top_topics) >= 3:
			title = f"Темы: {', '.join(top_topics[:2])} и другие"
		else:
			title = f"Цепочка {chain['chain_id']}"

		# Добавить количество анализов если много
		if chain["analyses_count"] > 5:
			title += f" ({chain['analyses_count']} анализов)"

		chain["title"] = title

		result.append(chain)
	
	return result


@router.get("/topic-chains/{chain_id}", response_model=dict)
async def get_topic_chain_details(
		chain_id: str,
):
	"""
	Получить детальную информацию о цепочке тем.
	Args:
		chain_id: ID цепочки тем

	Returns:
		Детальная информация о цепочке с эволюцией тем
	"""

	# Получить все аналитики для цепочки
	analytics = await AIAnalytics.objects.filter(topic_chain_id=chain_id).order_by(AIAnalytics.analysis_date.asc())

	if not analytics:
		raise HTTPException(status_code=404, detail="Topic chain not found")

	# Получить информацию об источнике с платформой
	source = await Source.objects.select_related(Source.platform).get(id=analytics[0].source_id)
	source_info = {
		"id": source.id,
		"name": source.name,
		"platform": source.platform.name if source.platform else "unknown",
		"platform_type": source.platform.platform_type.db_value if source.platform else "unknown",
		"external_id": source.external_id,
		"base_url": source.platform.base_url if source.platform else ""
	}

	# Получить данные цепочки через сервис
	chain_data = topic_chain_service.build_topic_chain(analytics)

	if chain_id not in chain_data:
		raise HTTPException(status_code=404, detail="Chain data not found")

	# Добавить статистику по темам
	topic_stats = topic_chain_service.get_topic_statistics(chain_data[chain_id])

	return {
		"chain_id": chain_id,
		"source_info": source_info,
		"chain_data": chain_data[chain_id],
		"topic_statistics": topic_stats,
		"total_analyses": len(analytics)
	}


@router.get("/topic-chains/{chain_id}/evolution", response_model=list[dict])
async def get_topic_chain_evolution(
		chain_id: str,
):
	"""
	Получить эволюцию тем в цепочке для построения графиков.

	Args:
		chain_id: ID цепочки тем

	Returns:
		Данные для визуализации эволюции тем
	"""

	try:
		logger.info(f"Getting evolution for chain_id: {chain_id}")

		# Получить все аналитики для цепочки
		analytics = await AIAnalytics.objects.filter(topic_chain_id=chain_id).order_by(AIAnalytics.analysis_date.asc())

		logger.info(f"Found {len(analytics)} analytics for chain {chain_id}")

		if not analytics:
			logger.warning(f"No analytics found for chain_id: {chain_id}")
			raise HTTPException(status_code=404, detail="Topic chain not found")

		# Получить данные цепочки через сервис
		chain_data = topic_chain_service.build_topic_chain(analytics)

		logger.info(f"Chain data built, chain_id in chain_data: {chain_id in chain_data}")

		if chain_id not in chain_data:
			logger.error(f"Chain {chain_id} not found in chain_data: {list(chain_data.keys())}")
			raise HTTPException(status_code=404, detail="Chain data not found")

		evolution_data = []
		chain_evolution = chain_data.get(chain_id, {}).get("evolution", [])

		logger.info(f"Chain evolution data: {len(chain_evolution)} items")

		for i, analysis in enumerate(chain_evolution):
			try:
				logger.info(f"Processing evolution item {i}: {analysis.get('date', 'no_date')}")

				# Extract topics as simple strings for UI
				topic_names = []
				topics_data = analysis.get("topics", [])

				logger.info(f"Processing analysis with topics: {topics_data}")

				for topic in topics_data:
					if isinstance(topic, dict):
						topic_name = topic.get("topic", "")
						if topic_name:
							topic_names.append(topic_name)
					elif isinstance(topic, str):
						topic_names.append(topic)

				logger.info(f"Extracted topic names: {topic_names}")

				# Get sentiment score from metrics or calculate from topics
				sentiment_score = 0.0
				metrics = analysis.get("metrics", {})

				if "sentiment_score" in metrics:
					sentiment_score = metrics.get("sentiment_score", 0.0)
					logger.info(f"Using sentiment from metrics: {sentiment_score}")
				else:
					# Calculate average sentiment from topics
					sentiments = []
					for topic in topics_data:
						if isinstance(topic, dict) and "sentiment" in topic:
							sent = topic.get("sentiment")
							# Convert sentiment labels to scores
							if isinstance(sent, str):
								if sent.lower() in ["positive", "положительный"]:
									sentiments.append(0.7)
								elif sent.lower() in ["negative", "отрицательный"]:
									sentiments.append(-0.7)
								else:
									sentiments.append(0.0)
							elif isinstance(sent, (int, float)):
								sentiments.append(float(sent))

					if sentiments:
						sentiment_score = sum(sentiments) / len(sentiments)
						logger.info(f"Calculated sentiment from topics: {sentiment_score}")

				evolution_data.append({
					"analysis_date": analysis.get("date", ""),
					"topics": topics_data,  # Возвращаем полные объекты тем, а не только названия
					"sentiment_score": sentiment_score,
					"post_url": None  # TODO: Add post URL if available
				})

				logger.info(f"Evolution item {i}: date={analysis.get('date')}, topics_count={len(topics_data)}, sentiment={sentiment_score}")

			except Exception as e:
				logger.error(f"Error processing analysis evolution item {i}: {e}")
				logger.error(f"Problematic analysis data: {analysis}")
				continue

		logger.info(f"Returning {len(evolution_data)} evolution items")
		return evolution_data

	except Exception as e:
		logger.error(f"Error in get_topic_chain_evolution for chain_id {chain_id}: {e}", exc_info=True)
		raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/debug/topic-chain/{chain_id}", response_model=dict)
async def debug_topic_chain(chain_id: str):
	"""
	Диагностический эндпоинт для проверки данных цепочки тем.

	Returns:
		Полные данные цепочки для диагностики
	"""
	# Получить все аналитики для цепочки
	analytics = await AIAnalytics.objects.filter(topic_chain_id=chain_id).order_by(AIAnalytics.analysis_date.asc())

	if not analytics:
		raise HTTPException(status_code=404, detail="Topic chain not found")

	# Получить данные цепочки через сервис
	chain_data = topic_chain_service.build_topic_chain(analytics)

	if chain_id not in chain_data:
		logger.error(f"Chain {chain_id} not found in chain_data: {list(chain_data.keys())}")
		raise HTTPException(status_code=404, detail="Chain data not found")

	return {
		"chain_id": chain_id,
		"total_analytics": len(analytics),
		"analytics_dates": [str(a.analysis_date) for a in analytics],
		"chain_data": chain_data,
		"evolution_data": chain_data.get(chain_id, {}).get("evolution", []),
		"debug_info": {
			"analytics_summary_keys": [list(getattr(a, 'summary_data', {}).keys()) if hasattr(a, 'summary_data') and a.summary_data else [] for a in analytics],
			"chain_data_keys": list(chain_data.keys()) if chain_data else []
		}
	}


@router.get("/debug/analytics", response_model=list[dict])
async def debug_analytics_data():
	"""
	Диагностический эндпоинт для проверки данных аналитики.

	Returns:
		Сырые данные аналитики для диагностики
	"""
	# Получить первые 5 записей аналитики
	analytics = await AIAnalytics.objects.filter().limit(5)

	result = []
	for a in analytics:
		result.append({
			"id": a.id,
			"source_id": a.source_id,
			"topic_chain_id": getattr(a, 'topic_chain_id', None),
			"analysis_date": str(a.analysis_date) if a.analysis_date else None,
			"has_summary_data": bool(getattr(a, 'summary_data', None)),
			"has_response_payload": bool(getattr(a, 'response_payload', None)),
			"summary_keys": list(getattr(a, 'summary_data', {}).keys()) if getattr(a, 'summary_data', None) else [],
			"response_keys": list(getattr(a, 'response_payload', {}).keys())
			if getattr(a, 'response_payload', None) else []
		})

@router.get("/debug/analytics/{analytics_id}", response_model=dict)
async def debug_single_analytics(analytics_id: int):
	"""
	Диагностический эндпоинт для проверки данных конкретной аналитики.
	"""
	analytics = await AIAnalytics.objects.get(id=analytics_id)

	result = {
		"id": analytics.id,
		"source_id": analytics.source_id,
		"topic_chain_id": getattr(analytics, 'topic_chain_id', None),
		"analysis_date": str(analytics.analysis_date) if analytics.analysis_date else None,
		"summary_data": getattr(analytics, 'summary_data', None),
		"response_payload": getattr(analytics, 'response_payload', None),
	}

	return result

@router.get("/analytics/aggregate/sentiment-trends", response_model=dict)
async def get_sentiment_trends_aggregate(
	source_id: Optional[int] = Query(None, description="Filter by source"),
	scenario_id: Optional[int] = Query(None, description="Filter by scenario"),
	days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
	group_by: str = Query('day', description="Group by: day, week"),
	session: AsyncSession = Depends(get_db),
):
	"""
	Get aggregated sentiment trends over time.
	
	Returns daily/weekly sentiment averages with distribution.
	"""
	logger.info(
		f"Requesting sentiment trends (source={source_id}, scenario={scenario_id}, days={days})"
	)
	
	aggregator = ReportAggregator(session=session)
	trends = await aggregator.get_sentiment_trends(
		source_id=source_id,
		scenario_id=scenario_id,
		days=days,
		group_by=group_by
	)
	
	return {
		"trends": trends,
		"period_days": days,
		"group_by": group_by
	}


@router.get("/analytics/aggregate/top-topics", response_model=dict)
async def get_top_topics_aggregate(
	source_id: Optional[int] = Query(None, description="Filter by source"),
	scenario_id: Optional[int] = Query(None, description="Filter by scenario"),
	days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
	limit: int = Query(10, ge=1, le=50, description="Max topics to return"),
	session: AsyncSession = Depends(get_db),
):
	"""
	Get top topics/keywords with sentiment and examples.
	
	Returns most mentioned topics with average sentiment scores.
	"""
	logger.info(
		f"Requesting top topics (source={source_id}, scenario={scenario_id}, days={days})"
	)
	
	aggregator = ReportAggregator(session=session)
	topics = await aggregator.get_top_topics(
		source_id=source_id,
		scenario_id=scenario_id,
		days=days,
		limit=limit
	)
	
	return {
		"topics": topics,
		"period_days": days,
		"total_topics": len(topics)
	}


@router.get("/analytics/aggregate/llm-stats", response_model=dict)
async def get_llm_provider_stats_aggregate(
	source_id: Optional[int] = Query(None, description="Filter by source"),
	scenario_id: Optional[int] = Query(None, description="Filter by scenario"),
	days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
	session: AsyncSession = Depends(get_db),
):
	"""
	Get LLM provider usage statistics and costs.
	
	Returns provider breakdown with token usage and estimated costs.
	"""
	logger.info(
		f"Requesting LLM stats (source={source_id}, scenario={scenario_id}, days={days})"
	)
	
	aggregator = ReportAggregator(session=session)
	stats = await aggregator.get_llm_provider_stats(
		source_id=source_id,
		scenario_id=scenario_id,
		days=days
	)
	
	return stats


@router.get("/analytics/aggregate/content-mix", response_model=dict)
async def get_content_mix_aggregate(
	source_id: Optional[int] = Query(None, description="Filter by source"),
	scenario_id: Optional[int] = Query(None, description="Filter by scenario"),
	days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
	session: AsyncSession = Depends(get_db),
):
	"""
	Get content type distribution (text/image/video).
	
	Returns percentage breakdown of analyzed media types.
	"""
	logger.info(
		f"Requesting content mix (source={source_id}, scenario={scenario_id}, days={days})"
	)
	
	aggregator = ReportAggregator(session=session)
	mix = await aggregator.get_content_mix(
		source_id=source_id,
		scenario_id=scenario_id,
		days=days
	)
	
	return mix


@router.get("/analytics/aggregate/engagement", response_model=dict)
async def get_engagement_metrics_aggregate(
	source_id: Optional[int] = Query(None, description="Filter by source"),
	scenario_id: Optional[int] = Query(None, description="Filter by scenario"),
	days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
	session: AsyncSession = Depends(get_db),
):
	"""
	Get engagement metrics (reactions, comments).
	
	Returns average engagement rates per post.
	"""
	logger.info(
		f"Requesting engagement metrics (source={source_id}, scenario={scenario_id}, days={days})"
	)
	
	aggregator = ReportAggregator(session=session)
	metrics = await aggregator.get_engagement_metrics(
		source_id=source_id,
		scenario_id=scenario_id,
		days=days
	)
	
	return metrics


@router.get("/scenarios", response_model=list[dict])
async def get_scenarios_list():
	"""
	Get list of bot scenarios for filters.
	
	Returns:
		List of scenarios with id and name
	"""
	from app.models import BotScenario
	
	scenarios = await BotScenario.objects.filter(is_active=True).order_by(BotScenario.name.asc())
	
	return [
		{
			"id": scenario.id,
			"name": scenario.name,
			"description": scenario.description or ""
		}
		for scenario in scenarios
	]
