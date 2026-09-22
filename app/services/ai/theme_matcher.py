"""
Theme matching service for AI analytics.

Сервис для связывания аналитик по темам используя PostgreSQL array operations.
"""

import logging
from typing import Optional

from app.models import AIAnalytics

logger = logging.getLogger(__name__)


class ThemeMatcher:
	"""
	Matches AI analytics records by main_topics similarity.

	Uses PostgreSQL array operations for efficient topic-based matching.
	"""

	@staticmethod
	async def find_similar_analytics(
			new_topics: list[str],
			source_id: int,
			similarity_threshold: float = 0.3,
			lookback_days: int = 30
	) -> Optional[AIAnalytics]:
		"""
		Find similar AI analytics by main_topics overlap.

		Args:
			new_topics: List of topics from new analysis
			source_id: Source ID to search within
			similarity_threshold: Minimum similarity threshold
			lookback_days: How many days back to search

		Returns:
			AIAnalytics record if similar one found, None otherwise
		"""
		if not new_topics:
			logger.debug("No topics provided for similarity search")
			return None

		try:
			# Use PostgreSQL array overlap operator (&&) for efficient matching
			# This finds records where main_topics array overlaps with new_topics
			similar_analytics = await AIAnalytics.objects.raw_sql("""
				WITH ranked_analytics AS (
					SELECT *, 
						CARDINALITY(main_topics & %s)::float / 
						GREATEST(CARDINALITY(main_topics | %s), 1) as similarity
					FROM social_manager.ai_analytics 
					WHERE source_id = %s 
					AND main_topics && %s
					AND main_topics IS NOT NULL
				)
				SELECT * FROM ranked_analytics
				WHERE similarity >= %s
				ORDER BY similarity DESC, created_at DESC
				LIMIT 1
			""", [new_topics, new_topics, source_id, new_topics, similarity_threshold])

			if similar_analytics:
				logger.info(
					f"Found similar analytics for source {source_id}: "
					f"{len(similar_analytics[0].main_topics or [])} overlapping topics"
				)
				return similar_analytics[0]

			logger.debug(f"No similar analytics found for source {source_id}")
			return None

		except Exception as e:
			logger.error(f"Error in similarity search: {e}")
			return None

	@staticmethod
	async def calculate_topic_similarity(topics1: list[str], topics2: list[str]) -> float:
		"""
		Calculate similarity score between two topic lists.

		Args:
			topics1: First list of topics
			topics2: Second list of topics

		Returns:
			Similarity score from 0.0 to 1.0
		"""
		if not topics1 or not topics2:
			return 0.0

		set1 = set(topics1)
		set2 = set(topics2)

		intersection = len(set1.intersection(set2))
		union = len(set1.union(set2))

		return intersection / union if union > 0 else 0.0
