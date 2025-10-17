"""
Content collection scheduler.

Automatically collects and analyzes content from active sources
using checkpoint system to avoid duplicates and minimize LLM costs.
"""
import logging
import asyncio
from datetime import datetime, timezone
from typing import List

from app.models import Source
from app.services.checkpoint_manager import CheckpointManager, CollectionResult
from app.services.ai.analyzer import AIAnalyzer
from app.services.social.factory import get_social_client
from app.services.ai.optimizer import LLMOptimizer
from app.services.ai.trigger_evaluator import trigger_evaluator

logger = logging.getLogger(__name__)


def _platform_type_value(platform) -> str:
	"""Return normalized platform_type string (e.g., 'vk', 'telegram')."""
	p = getattr(platform, 'platform_type', None)
	if hasattr(p, 'db_value'):
		return str(p.db_value).lower()
	if hasattr(p, 'value'):
		return str(p.value).lower()
	return str(p).lower()


class ContentScheduler:
	"""
	Scheduler for automatic content collection and analysis.
	
	Features:
	- Checkpoint-based collection (only new content)
	- Respects collection_interval_hours from BotScenario
	- LLM optimization (rate limiting, batching, cost tracking)
	- Error handling and retry logic
	- Comprehensive logging
	"""

	def __init__(self):
		"""Initialize scheduler with analyzer and optimizer."""
		self.analyzer = AIAnalyzer()
		self.optimizer = LLMOptimizer()

	async def run_collection_cycle(self) -> dict:
		"""
		Run one collection cycle for all eligible sources.
		
		This method:
		1. Gets all active sources
		2. Checks which ones need collection (via checkpoint)
		3. Collects new content from each
		4. Analyzes with LLM (optimized)
		5. Saves checkpoints
		
		Returns:
			Dict with collection statistics:
			{
				"total_sources": int,
				"collected": int,
				"skipped": int,
				"failed": int,
				"total_content": int,
				"total_cost": float
			}
		"""
		logger.info("=" * 60)
		logger.info("STARTING COLLECTION CYCLE")
		logger.info("=" * 60)

		stats = {
			"total_sources": 0,
			"collected": 0,
			"skipped": 0,
			"failed": 0,
			"total_content": 0,
			"total_cost": 0.0,
			"started_at": datetime.now(timezone.utc).isoformat()
		}

		try:
			# Get all active sources with required relations preloaded to avoid DetachedInstanceError
			sources = await (
				Source.objects
				.select_related('platform', 'bot_scenario')
				.filter(is_active=True)
			)
			stats["total_sources"] = len(sources)

			logger.info(f"Found {len(sources)} active sources")

			# Process each source
			for source in sources:
				try:
					result = await self._collect_source(source)

					if result:
						stats["collected"] += 1
						stats["total_content"] += result["content_count"]
						stats["total_cost"] += result.get("cost", 0.0)
					else:
						stats["skipped"] += 1

				except Exception as e:
					logger.error(f"Failed to collect source {source.id}: {e}", exc_info=True)
					stats["failed"] += 1

			# Get cost summary from optimizer
			cost_summary = self.optimizer.cost_tracker.get_session_summary()
			stats["total_cost"] = cost_summary.get("total_cost", 0.0)
			stats["ended_at"] = datetime.now(timezone.utc).isoformat()

			logger.info("=" * 60)
			logger.info("COLLECTION CYCLE COMPLETE")
			logger.info(f"Collected: {stats['collected']}/{stats['total_sources']}")
			logger.info(f"Skipped: {stats['skipped']} | Failed: {stats['failed']}")
			logger.info(f"Total content: {stats['total_content']} items")
			logger.info(f"Total cost: ${stats['total_cost']:.4f}")
			logger.info("=" * 60)

			return stats

		except Exception as e:
			logger.error(f"Collection cycle failed: {e}", exc_info=True)
			stats["error"] = str(e)
			return stats

	async def _collect_source(self, source: Source) -> dict | None:
		"""
		Collect and analyze content from single source.
		
		Args:
			source: Source to collect from
			
		Returns:
			Dict with collection result or None if skipped
		"""
		logger.info(f"Processing source {source.id}: {source.name}")

		# Check if collection needed (via checkpoint)
		if not CheckpointManager.should_collect(source):
			logger.info(
				f"Source {source.id} collected recently "
				f"(last: {source.last_checked}), skipping"
			)
			return None

		logger.info(f"Source {source.id} needs collection")

		try:
			# Get platform client (pass Platform instance, not name)
			client = get_social_client(source.platform)
			if not client:
				logger.error(f"No client for platform {source.platform.name}")
				return None

			# Get checkpoint for incremental collection
			checkpoint = await CheckpointManager.get_checkpoint(source)

			# Collect new content (platform-specific)
			content = await self._collect_platform_content(
				client=client,
				source=source,
				checkpoint=checkpoint
			)

			if not content:
				logger.info(f"No new content for source {source.id}")

				# Update checkpoint anyway to avoid rechecking
				result = CollectionResult(
					source_id=source.id,
					content_count=0,
					has_new_content=False
				)
				await result.save_checkpoint()
				return None

			logger.info(f"Collected {len(content)} items from source {source.id}")

			# 🆕 Apply trigger filtering BEFORE LLM analysis
			if source.bot_scenario:
				filtered_content = await trigger_evaluator.should_analyze(
					content=content,
					scenario=source.bot_scenario
				)

				if len(filtered_content) < len(content):
					logger.info(
						f"Trigger filter: {len(filtered_content)}/{len(content)} items "
						f"({len(content) - len(filtered_content)} skipped, saved tokens!)"
					)

				content = filtered_content

			if not content:
				logger.info(f"No content passed trigger filter for source {source.id}")
				return None

			# Analyze with LLM (optimized) - only filtered content
			analysis = await self.analyzer.analyze_content(
				content=content,
				source=source
			)

			if not analysis:
				logger.error(f"Analysis failed for source {source.id}")
				return None

			# Save checkpoint on success
			result = CollectionResult(
				source_id=source.id,
				content_count=len(content),
				has_new_content=True
			)
			await result.save_checkpoint()

			logger.info(f"✅ Successfully processed source {source.id}")

			return {
				"source_id": source.id,
				"content_count": len(content),
				"analysis_id": analysis.id,
				"cost": 0.0  # Cost tracked by optimizer
			}

		except Exception as e:
			logger.error(f"Failed to collect source {source.id}: {e}", exc_info=True)
			return None

	async def _collect_platform_content(
			self,
			client,
			source: Source,
			checkpoint: dict
		) -> list[dict]:
		"""
		Collect content from platform using checkpoint.
		
		Args:
			client: Social media client
			source: Source to collect from
			checkpoint: Checkpoint data
			
		Returns:
			List of collected content items
		"""
		last_checked = checkpoint.get("last_checked")

		# Delegate to platform client as done in admin check_source_action:
		# temporarily merge checkpoint into source.params['collection'] and call client.collect_data()
		try:
			original_params = source.params.copy() if source.params else {}
			if not source.params:
				source.params = {}
			if 'collection' not in source.params:
				source.params['collection'] = {}

			# Merge checkpoint params (cursors) and since timestamp
			cp_params = checkpoint.get('params', {}) or {}
			source.params['collection'].update(cp_params)
			if last_checked:
				# Common key that clients can use to filter since last collection
				source.params['collection']['since'] = last_checked

			# Let the concrete client handle request building and normalization
			content = await client.collect_data(source=source, content_type="posts")
			return content or []

		except Exception as e:
			logger.error(f"Platform collection failed: {e}")
			return []
		finally:
			# Restore original params to avoid side effects
			source.params = original_params

	async def run_forever(self, interval_minutes: int = 60):
		"""
		Run scheduler in continuous loop.
		
{{ ... }}
			interval_minutes: Interval between collection cycles (default: 60)
		"""
		logger.info(f"Starting scheduler with {interval_minutes}min interval")

		while True:
			try:
				await self.run_collection_cycle()

				# Wait before next cycle
				logger.info(f"Sleeping for {interval_minutes} minutes...")
				await asyncio.sleep(interval_minutes * 60)

			except KeyboardInterrupt:
				logger.info("Scheduler stopped by user")
				break
			except Exception as e:
				logger.error(f"Scheduler error: {e}", exc_info=True)
				# Wait a bit before retrying
				await asyncio.sleep(60)


# Scheduler instance
scheduler = ContentScheduler()


async def start_scheduler(interval_minutes: int = 60):
	"""
	Start content collection scheduler.
	
	Args:
		interval_minutes: Interval between collection cycles
	"""
	await scheduler.run_forever(interval_minutes)


if __name__ == "__main__":
	# Run scheduler standalone
	import sys

	interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60

	logger.info(f"Starting content scheduler (interval: {interval}min)")
	asyncio.run(start_scheduler(interval))
