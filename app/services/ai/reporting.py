"""
Aggregation and reporting service for AI analytics.

Provides aggregated metrics for dashboard and admin panels:
- Sentiment trends over time
- Top topics and categories
- LLM provider efficiency and costs
- Content mix (text/image/video)
- Engagement metrics
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Any
from collections import defaultdict, Counter

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AIAnalytics, Source, BotScenario
from app.types import PeriodType, MediaType
from app.core.database import get_db
from app.utils.enum_helpers import get_enum_value

logger = logging.getLogger(__name__)


class ReportAggregator:
	"""
	Service for aggregating AI analytics data into actionable reports.
	
	Supports:
	- Sentiment trends over time
	- Topic discovery and tracking
	- LLM cost and efficiency analysis
	- Content type distribution
	- Engagement metrics
	"""
	
	def __init__(self, session: Optional[AsyncSession] = None):
		"""Initialize aggregator with database session."""
		self.session = session
	
	async def get_sentiment_trends(
		self,
		source_id: Optional[int] = None,
		scenario_id: Optional[int] = None,
		days: int = 7,
		group_by: str = 'day'
	) -> list[dict[str, Any]]:
		"""
		Get sentiment trends over time period.
		
		Args:
			source_id: Filter by specific source
			scenario_id: Filter by scenario (via source)
			days: Number of days to look back
			group_by: Grouping interval ('day', 'week')
		
		Returns:
			List of trend points with date, avg sentiment, counts
		"""
		session = self.session or await anext(get_db())
		
		try:
			# Build query
			query = select(AIAnalytics).where(
				AIAnalytics.analysis_date >= date.today() - timedelta(days=days)
			)
			
			# Apply filters
			if source_id:
				query = query.where(AIAnalytics.source_id == source_id)
			
			if scenario_id:
				# Join with Source to filter by scenario
				query = query.join(Source).where(Source.bot_scenario_id == scenario_id)
			
			# Order by date
			query = query.order_by(AIAnalytics.analysis_date.asc())
			
			# Execute
			result = await session.execute(query)
			analytics = result.scalars().all()
			
			# Aggregate by date
			trends = []
			by_date = defaultdict(list)
			
			for a in analytics:
				# Extract sentiment from summary_data
				sentiment_data = self._extract_sentiment(a.summary_data)
				if sentiment_data:
					by_date[a.analysis_date].append(sentiment_data)
			
			# Calculate averages per date
			for analysis_date, sentiments in sorted(by_date.items()):
				avg_score = sum(s['score'] for s in sentiments) / len(sentiments) if sentiments else 0
				
				# Count sentiment distribution
				positive = sum(1 for s in sentiments if s['label'] == 'positive')
				neutral = sum(1 for s in sentiments if s['label'] == 'neutral')
				negative = sum(1 for s in sentiments if s['label'] == 'negative')
				
				trends.append({
					'date': analysis_date.isoformat(),
					'avg_sentiment_score': round(avg_score, 2),
					'total_analyses': len(sentiments),
					'distribution': {
						'positive': positive,
						'neutral': neutral,
						'negative': negative,
					}
				})
			
			return trends
			
		except Exception as e:
			logger.error(f"Error getting sentiment trends: {e}", exc_info=True)
			return []
		finally:
			if not self.session:
				await session.close()
	
	async def get_top_topics(
		self,
		source_id: Optional[int] = None,
		scenario_id: Optional[int] = None,
		days: int = 7,
		limit: int = 10
	) -> list[dict[str, Any]]:
		"""
		Get top topics/keywords from analyses.
		
		Args:
			source_id: Filter by specific source
			scenario_id: Filter by scenario
			days: Number of days to look back
			limit: Max number of topics to return
		
		Returns:
			List of topics with counts, sentiment, example posts
		"""
		session = self.session or await anext(get_db())
		
		try:
			# Build query
			query = select(AIAnalytics).where(
				AIAnalytics.analysis_date >= date.today() - timedelta(days=days)
			)
			
			if source_id:
				query = query.where(AIAnalytics.source_id == source_id)
			
			if scenario_id:
				query = query.join(Source).where(Source.bot_scenario_id == scenario_id)
			
			# Execute
			result = await session.execute(query)
			analytics = result.scalars().all()
			
			# Extract and count topics
			topic_counter = Counter()
			topic_sentiments = defaultdict(list)
			topic_examples = defaultdict(list)
			
			for a in analytics:
				topics = self._extract_topics(a.summary_data)
				sentiment = self._extract_sentiment(a.summary_data)
				
				for topic in topics:
					topic_counter[topic] += 1
					
					if sentiment:
						topic_sentiments[topic].append(sentiment['score'])
					
					# Store example (limit to 2 per topic)
					if len(topic_examples[topic]) < 2:
						example = self._extract_example_text(a.summary_data)
						if example:
							topic_examples[topic].append(example)
			
			# Build result
			top_topics = []
			for topic, count in topic_counter.most_common(limit):
				avg_sentiment = (
					sum(topic_sentiments[topic]) / len(topic_sentiments[topic])
					if topic_sentiments[topic] else 0
				)
				
				top_topics.append({
					'topic': topic,
					'count': count,
					'avg_sentiment': round(avg_sentiment, 2),
					'examples': topic_examples[topic][:2]
				})
			
			return top_topics
			
		except Exception as e:
			logger.error(f"Error getting top topics: {e}", exc_info=True)
			return []
		finally:
			if not self.session:
				await session.close()
	
	async def get_llm_provider_stats(
		self,
		source_id: Optional[int] = None,
		scenario_id: Optional[int] = None,
		days: int = 30
	) -> dict[str, Any]:
		"""
		Get LLM provider usage statistics and costs.
		
		Args:
			source_id: Filter by specific source
			scenario_id: Filter by scenario
			days: Number of days to look back
		
		Returns:
			Dict with provider stats, costs, token usage
		"""
		session = self.session or await anext(get_db())
		
		try:
			# Build query
			query = select(AIAnalytics).where(
				AIAnalytics.analysis_date >= date.today() - timedelta(days=days)
			)
			
			if source_id:
				query = query.where(AIAnalytics.source_id == source_id)
			
			if scenario_id:
				query = query.join(Source).where(Source.bot_scenario_id == scenario_id)
			
			# Execute
			result = await session.execute(query)
			analytics = result.scalars().all()
			
			# Aggregate by provider
			provider_stats = defaultdict(lambda: {
				'requests': 0,
				'total_tokens': 0,
				'request_tokens': 0,
				'response_tokens': 0,
				'estimated_cost': 0,
				'models': Counter()
			})
			
			for a in analytics:
				provider = a.provider_type or 'unknown'
				stats = provider_stats[provider]
				
				stats['requests'] += 1
				stats['request_tokens'] += a.request_tokens or 0
				stats['response_tokens'] += a.response_tokens or 0
				stats['total_tokens'] += (a.request_tokens or 0) + (a.response_tokens or 0)
				stats['estimated_cost'] += a.estimated_cost or 0
				
				if a.llm_model:
					stats['models'][a.llm_model] += 1
			
			# Convert to serializable format
			result_stats = {}
			total_cost = 0
			total_requests = 0
			
			for provider, stats in provider_stats.items():
				total_cost += stats['estimated_cost']
				total_requests += stats['requests']
				
				result_stats[provider] = {
					'requests': stats['requests'],
					'total_tokens': stats['total_tokens'],
					'request_tokens': stats['request_tokens'],
					'response_tokens': stats['response_tokens'],
					'estimated_cost_usd': round(stats['estimated_cost'] / 100, 4),  # cents to USD
					'avg_tokens_per_request': (
						round(stats['total_tokens'] / stats['requests'], 1)
						if stats['requests'] > 0 else 0
					),
					'models': dict(stats['models'].most_common(5))
				}
			
			return {
				'providers': result_stats,
				'summary': {
					'total_requests': total_requests,
					'total_cost_usd': round(total_cost / 100, 2),
					'period_days': days
				}
			}
			
		except Exception as e:
			logger.error(f"Error getting LLM provider stats: {e}", exc_info=True)
			return {'providers': {}, 'summary': {}}
		finally:
			if not self.session:
				await session.close()
	
	async def get_content_mix(
		self,
		source_id: Optional[int] = None,
		scenario_id: Optional[int] = None,
		days: int = 7
	) -> dict[str, Any]:
		"""
		Get content type distribution (text/image/video).
		
		Args:
			source_id: Filter by specific source
			scenario_id: Filter by scenario
			days: Number of days to look back
		
		Returns:
			Dict with counts and percentages per media type
		"""
		session = self.session or await anext(get_db())
		
		try:
			# Build query
			query = select(AIAnalytics).where(
				AIAnalytics.analysis_date >= date.today() - timedelta(days=days)
			)
			
			if source_id:
				query = query.where(AIAnalytics.source_id == source_id)
			
			if scenario_id:
				query = query.join(Source).where(Source.bot_scenario_id == scenario_id)
			
			# Execute
			result = await session.execute(query)
			analytics = result.scalars().all()
			
			# Count media types
			media_counts = Counter()
			total = 0
			
			for a in analytics:
				if a.media_types:
					for media_type in a.media_types:
						media_counts[media_type] += 1
						total += 1
			
			# Calculate percentages
			media_mix = {}
			for media_type, count in media_counts.items():
				percentage = round((count / total * 100), 1) if total > 0 else 0
				media_mix[media_type] = {
					'count': count,
					'percentage': percentage
				}
			
			return {
				'media_types': media_mix,
				'total_analyses': len(analytics),
				'total_media_items': total
			}
			
		except Exception as e:
			logger.error(f"Error getting content mix: {e}", exc_info=True)
			return {'media_types': {}, 'total_analyses': 0, 'total_media_items': 0}
		finally:
			if not self.session:
				await session.close()
	
	async def get_engagement_metrics(
		self,
		source_id: Optional[int] = None,
		scenario_id: Optional[int] = None,
		days: int = 7
	) -> dict[str, Any]:
		"""
		Get engagement metrics from analyzed content.
		
		Args:
			source_id: Filter by specific source
			scenario_id: Filter by scenario
			days: Number of days to look back
		
		Returns:
			Dict with avg reactions, comments, engagement rates
		"""
		session = self.session or await anext(get_db())
		
		try:
			# Build query
			query = select(AIAnalytics).where(
				AIAnalytics.analysis_date >= date.today() - timedelta(days=days)
			)
			
			if source_id:
				query = query.where(AIAnalytics.source_id == source_id)
			
			if scenario_id:
				query = query.join(Source).where(Source.bot_scenario_id == scenario_id)
			
			# Execute
			result = await session.execute(query)
			analytics = result.scalars().all()
			
			# Extract engagement data
			total_reactions = 0
			total_comments = 0
			total_posts = 0
			
			for a in analytics:
				engagement = self._extract_engagement(a.summary_data)
				if engagement:
					total_reactions += engagement.get('reactions', 0)
					total_comments += engagement.get('comments', 0)
					total_posts += engagement.get('posts', 1)
			
			avg_reactions = round(total_reactions / total_posts, 1) if total_posts > 0 else 0
			avg_comments = round(total_comments / total_posts, 1) if total_posts > 0 else 0
			
			return {
				'avg_reactions_per_post': avg_reactions,
				'avg_comments_per_post': avg_comments,
				'total_reactions': total_reactions,
				'total_comments': total_comments,
				'total_posts_analyzed': total_posts
			}
			
		except Exception as e:
			logger.error(f"Error getting engagement metrics: {e}", exc_info=True)
			return {}
		finally:
			if not self.session:
				await session.close()
	
	# Helper methods for data extraction
	
	def _extract_sentiment(self, summary_data: dict) -> Optional[dict]:
		"""Extract sentiment data from summary_data JSON."""
		if not summary_data:
			return None
		
		# Try new structure first (v3.0-multi-llm)
		multi_llm = summary_data.get('multi_llm_analysis', {})
		text_analysis = multi_llm.get('text_analysis', {})
		
		# New structure: sentiment_score in text_analysis
		if 'sentiment_score' in text_analysis:
			score = text_analysis['sentiment_score']
			# Determine label from score
			if score > 0.6:
				label = 'positive'
			elif score < 0.4:
				label = 'negative'
			else:
				label = 'neutral'
			return {'label': label, 'score': score}
		
		# Fallback: Try to infer from overall_mood (text description)
		if 'overall_mood' in text_analysis:
			mood_text = str(text_analysis['overall_mood']).lower()
			# Simple heuristic based on keywords
			if any(word in mood_text for word in ['позитивн', 'хорош', 'положительн', 'радост', 'оптимист']):
				return {'label': 'positive', 'score': 0.7}
			elif any(word in mood_text for word in ['негативн', 'плох', 'отрицательн', 'грустн', 'пессимист']):
				return {'label': 'negative', 'score': 0.3}
			else:
				return {'label': 'neutral', 'score': 0.5}
		
		# Fallback: Try old structure
		ai_analysis = summary_data.get('ai_analysis', {})
		
		# Path 1: sentiment_analysis object
		sentiment = ai_analysis.get('sentiment_analysis', {})
		if sentiment and 'overall_sentiment' in sentiment:
			overall = sentiment['overall_sentiment']
			return {
				'label': overall.get('label', 'neutral'),
				'score': overall.get('score', 0)
			}
		
		# Path 2: direct sentiment field
		if 'sentiment' in ai_analysis:
			return {
				'label': ai_analysis['sentiment'],
				'score': 0  # No score available
			}
		
		# No sentiment data found
		return None
	
	def _extract_topics(self, summary_data: dict) -> list[str]:
		"""Extract topics/keywords from summary_data JSON."""
		if not summary_data:
			return []
		
		topics = []
		
		# Try new structure first (v3.0-multi-llm)
		multi_llm = summary_data.get('multi_llm_analysis', {})
		text_analysis = multi_llm.get('text_analysis', {})
		
		# New structure: main_topics, highlights
		if 'main_topics' in text_analysis:
			main_topics = text_analysis['main_topics']
			if isinstance(main_topics, list):
				topics.extend(main_topics)
		
		if 'highlights' in text_analysis:
			highlights = text_analysis['highlights']
			if isinstance(highlights, list):
				topics.extend(highlights)
		
		if topics:
			return topics
		
		# Fallback: Try old structure
		ai_analysis = summary_data.get('ai_analysis', {})
		
		# Extract from various fields
		if 'key_topics' in ai_analysis:
			topics.extend(ai_analysis['key_topics'])
		
		if 'categories' in ai_analysis:
			topics.extend(ai_analysis['categories'])
		
		if 'keywords' in ai_analysis:
			topics.extend(ai_analysis['keywords'])
		
		return topics
	
	def _extract_example_text(self, summary_data: dict) -> Optional[str]:
		"""Extract example text from summary_data."""
		if not summary_data:
			return None
		
		# Try to get first post or summary
		ai_analysis = summary_data.get('ai_analysis', {})
		
		if 'summary' in ai_analysis:
			return ai_analysis['summary'][:200]  # Truncate
		
		return None
	
	def _extract_engagement(self, summary_data: dict) -> Optional[dict]:
		"""Extract engagement metrics from summary_data."""
		if not summary_data:
			return None
		
		# Try new structure first (v3.0-multi-llm)
		content_statistics = summary_data.get('content_statistics', {})
		
		if content_statistics:
			return {
				'reactions': content_statistics.get('total_reactions', 0),
				'comments': content_statistics.get('total_comments', 0),
				'posts': content_statistics.get('total_posts', 1)
			}
		
		# Fallback: Try old structure
		content_stats = summary_data.get('content_stats', {})
		
		return {
			'reactions': content_stats.get('total_reactions', 0),
			'comments': content_stats.get('total_comments', 0),
			'posts': content_stats.get('total_posts', 1)
		}
