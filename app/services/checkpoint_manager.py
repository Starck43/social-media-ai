"""
Checkpoint manager for tracking content collection progress.

Prevents re-analyzing same content by maintaining checkpoints per source.
Each source tracks:
- last_checked: Last successful collection timestamp
- content_cursor: Platform-specific cursor/offset for pagination
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.models import Source

logger = logging.getLogger(__name__)


class CheckpointManager:
	"""
	Manages content collection checkpoints to avoid duplicate processing.
	
	For each source, tracks:
	- Last checked timestamp (when was content last collected)
	- Platform-specific cursors (for pagination, e.g., VK offset, Instagram since_id)
	
	This ensures:
	- No duplicate content analysis
	- Efficient incremental collection
	- Resume from last checkpoint after failures
	"""
	
	@staticmethod
	async def get_checkpoint(source: Source) -> dict[str, Any]:
		"""
		Get checkpoint data for source.
		
		Args:
			source: Source to get checkpoint for
			
		Returns:
			Dict with checkpoint data:
			{
				"last_checked": datetime or None,
				"params": dict with platform-specific data (cursors, offsets)
			}
		"""
		return {
			"last_checked": source.last_checked,
			"params": source.params or {}  # Platform-specific data
		}
	
	@staticmethod
	async def update_checkpoint(
		source_id: int,
		timestamp: Optional[datetime] = None,
		params: Optional[dict[str, Any]] = None
	) -> bool:
		"""
		Update checkpoint after successful collection.
		
		Args:
			source_id: Source ID
			timestamp: Collection timestamp (default: now)
			params: Optional platform-specific params to merge
			
		Returns:
			True if updated successfully
		"""
		if timestamp is None:
			timestamp = datetime.now(timezone.utc)
		
		# Ensure timezone awareness
		if timestamp.tzinfo is None:
			timestamp = timestamp.replace(tzinfo=timezone.utc)
		
		# Update via SourceManager
		source = await Source.objects.update_last_checked(source_id, timestamp)
		
		# Merge platform-specific params if provided
		if params and source:
			current_params = source.params or {}
			current_params.update(params)
			await Source.objects.update_by_id(source_id, params=current_params)
			
			logger.info(
				f"Updated checkpoint for source {source_id}: "
				f"timestamp={timestamp}, params={list(params.keys())}"
			)
		
		return source is not None
	
	@staticmethod
	def should_collect(
		source: Source,
		collection_interval_hours: int = 1
	) -> bool:
		"""
		Check if source should be collected based on checkpoint.
		
		Uses source.bot_scenario.collection_interval_hours if available,
		otherwise uses provided default.
		
		Args:
			source: Source to check
			collection_interval_hours: Minimum interval between collections (hours)
			
		Returns:
			True if source should be collected now
		"""
		if not source.is_active:
			return False
		
		# Never collected before - collect now
		if source.last_checked is None:
			return True
		
		# Get interval from scenario if available
		if hasattr(source, 'bot_scenario') and source.bot_scenario:
			collection_interval_hours = source.bot_scenario.collection_interval_hours or collection_interval_hours
		
		# Check if enough time passed since last collection
		now = datetime.now(timezone.utc)
		last_check = source.last_checked
		
		# Ensure timezone awareness for comparison
		if last_check.tzinfo is None:
			last_check = last_check.replace(tzinfo=timezone.utc)
		
		hours_since_check = (now - last_check).total_seconds() / 3600
		
		return hours_since_check >= collection_interval_hours
	
	@staticmethod
	def get_collection_params(source: Source) -> dict[str, Any]:
		"""
		Get platform-specific collection parameters based on checkpoint.
		
		Extracts parameters from source.params and source.last_checked
		to build API request params for incremental collection.
		
		Args:
			source: Source to collect from
			
		Returns:
			Dict with platform-specific params for API call:
			- VK: {"offset": 0, "start_time": last_checked}
			- Instagram: {"max_id": last_post_id}
			- Telegram: {"offset_id": last_message_id}
		"""
		params = {}
		
		# Add timestamp filter if checkpoint exists
		if source.last_checked:
			params["since"] = source.last_checked
		
		# Add platform-specific cursors from source.params
		if source.params:
			# VK offset
			if "vk_offset" in source.params:
				params["offset"] = source.params["vk_offset"]
			
			# Instagram max_id
			if "instagram_max_id" in source.params:
				params["max_id"] = source.params["instagram_max_id"]
			
			# Telegram offset_id
			if "telegram_offset_id" in source.params:
				params["offset_id"] = source.params["telegram_offset_id"]
		
		return params


class CollectionResult:
	"""Result of content collection with checkpoint data."""
	
	def __init__(
		self,
		source_id: int,
		content_count: int,
		has_new_content: bool,
		checkpoint_params: Optional[dict[str, Any]] = None
	):
		"""
		Initialize collection result.
		
		Args:
			source_id: Source ID
			content_count: Number of items collected
			has_new_content: Whether new content was found
			checkpoint_params: Platform-specific checkpoint data to save
		"""
		self.source_id = source_id
		self.content_count = content_count
		self.has_new_content = has_new_content
		self.checkpoint_params = checkpoint_params or {}
		self.timestamp = datetime.now(timezone.utc)
	
	async def save_checkpoint(self) -> bool:
		"""
		Save checkpoint after collection.
		
		Returns:
			True if saved successfully
		"""
		if not self.has_new_content:
			logger.info(
				f"No new content for source {self.source_id}, "
				f"skipping checkpoint update"
			)
			return False
		
		return await CheckpointManager.update_checkpoint(
			source_id=self.source_id,
			timestamp=self.timestamp,
			params=self.checkpoint_params
		)
