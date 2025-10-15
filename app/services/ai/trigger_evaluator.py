"""
Trigger evaluation service for filtering content and actions.

This service handles:
- Pre-filtering: Filter content BEFORE LLM analysis (saves tokens!)
- Post-filtering: Conditional actions AFTER analysis
- Baseline tracking: Historical data for anomaly detection
"""
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.models import BotScenario, AIAnalytics
from app.types import BotTriggerType

logger = logging.getLogger(__name__)


class TriggerEvaluator:
	"""
	Evaluates trigger conditions to filter content and actions.
	
	Flow:
	1. should_analyze() - Pre-filter: check triggers WITHOUT LLM
	2. [LLM Analysis happens]
	3. should_act() - Post-filter: check results AFTER LLM
	"""
	
	async def should_analyze(
		self,
		content: list[dict],
		scenario: BotScenario
	) -> list[dict]:
		"""
		Pre-filter content based on triggers BEFORE LLM analysis.
		
		This saves LLM costs by analyzing only relevant content.
		
		Args:
			content: List of content items to filter
			scenario: Bot scenario with trigger configuration
		
		Returns:
			Filtered list of content items that passed trigger conditions
		"""
		if not scenario.trigger_type or not content:
			return content  # No trigger = analyze all
		
		trigger_type = scenario.trigger_type
		trigger_config = scenario.trigger_config or {}
		
		logger.info(
			f"Pre-filtering {len(content)} items with trigger: {trigger_type.name}"
		)
		
		# Pre-analysis triggers (work WITHOUT LLM)
		if trigger_type == BotTriggerType.KEYWORD_MATCH:
			filtered = await self._filter_by_keywords(content, trigger_config)
		
		elif trigger_type == BotTriggerType.USER_MENTION:
			filtered = await self._filter_by_mentions(content, trigger_config)
		
		elif trigger_type == BotTriggerType.ACTIVITY_SPIKE:
			# Check if there's a spike - if yes, analyze all content
			has_spike = await self._detect_activity_spike(content, scenario, trigger_config)
			filtered = content if has_spike else []
		
		elif trigger_type == BotTriggerType.TIME_BASED:
			# Time-based triggers are handled by scheduler
			filtered = content
		
		else:
			# SENTIMENT_THRESHOLD, MANUAL - check after analysis
			filtered = content
		
		logger.info(
			f"Pre-filter: {len(filtered)}/{len(content)} items passed "
			f"({len(content) - len(filtered)} skipped)"
		)
		
		return filtered
	
	async def should_act(
		self,
		analysis_result: dict,
		scenario: BotScenario
	) -> bool:
		"""
		Post-filter: check if action should be performed AFTER LLM analysis.
		
		Args:
			analysis_result: Result from LLM analysis
			scenario: Bot scenario with trigger configuration
		
		Returns:
			True if action should be performed, False otherwise
		"""
		if not scenario.trigger_type:
			return True  # No trigger = always act
		
		trigger_type = scenario.trigger_type
		trigger_config = scenario.trigger_config or {}
		
		# Post-analysis triggers (need LLM results)
		if trigger_type == BotTriggerType.SENTIMENT_THRESHOLD:
			return await self._check_sentiment_threshold(analysis_result, trigger_config)
		
		elif trigger_type == BotTriggerType.MANUAL:
			# Manual triggers don't auto-execute
			return False
		
		else:
			# Other triggers already filtered in pre-analysis
			return True
	
	# ============================================================================
	# Pre-analysis filters
	# ============================================================================
	
	async def _filter_by_keywords(
		self,
		content: list[dict],
		config: dict
	) -> list[dict]:
		"""
		Filter content by keyword matching.
		
		Config example:
		{
			"keywords": ["жалоба", "проблема", "не работает"],
			"mode": "any",  # "any" or "all"
			"case_sensitive": false
		}
		"""
		keywords = config.get("keywords", [])
		mode = config.get("mode", "any")
		case_sensitive = config.get("case_sensitive", False)
		
		if not keywords:
			return content
		
		filtered = []
		
		for item in content:
			text = item.get("text", "") or item.get("content", "")
			if not text:
				continue
			
			if not case_sensitive:
				text = text.lower()
				keywords = [k.lower() for k in keywords]
			
			# Check if keywords are present
			matches = [kw for kw in keywords if kw in text]
			
			if mode == "any" and len(matches) > 0:
				filtered.append(item)
			elif mode == "all" and len(matches) == len(keywords):
				filtered.append(item)
		
		return filtered
	
	async def _filter_by_mentions(
		self,
		content: list[dict],
		config: dict
	) -> list[dict]:
		"""
		Filter content by user mentions.
		
		Config example:
		{
			"usernames": ["@brand", "@support", "company"],
			"mode": "any"
		}
		"""
		usernames = config.get("usernames", [])
		mode = config.get("mode", "any")
		
		if not usernames:
			return content
		
		filtered = []
		
		# Build regex pattern for mentions
		patterns = []
		for username in usernames:
			# Remove @ if present
			clean = username.lstrip("@")
			# Match @username or just username
			patterns.append(rf"@{clean}\b|{clean}\b")
		
		combined_pattern = "|".join(patterns)
		regex = re.compile(combined_pattern, re.IGNORECASE)
		
		for item in content:
			text = item.get("text", "") or item.get("content", "")
			if not text:
				continue
			
			if regex.search(text):
				filtered.append(item)
		
		return filtered
	
	async def _detect_activity_spike(
		self,
		content: list[dict],
		scenario: BotScenario,
		config: dict
	) -> bool:
		"""
		Detect if there's an activity spike compared to baseline.
		
		Config example:
		{
			"baseline_period_hours": 24,
			"spike_multiplier": 3.0
		}
		"""
		baseline_period_hours = config.get("baseline_period_hours", 24)
		spike_multiplier = config.get("spike_multiplier", 3.0)
		
		current_count = len(content)
		
		# Get historical baseline from analytics
		baseline_count = await self._get_baseline_content_count(
			scenario,
			hours=baseline_period_hours
		)
		
		if baseline_count == 0:
			# No historical data - consider any content as spike
			return current_count > 0
		
		# Check if current count exceeds baseline by multiplier
		threshold = baseline_count * spike_multiplier
		is_spike = current_count >= threshold
		
		if is_spike:
			logger.warning(
				f"Activity spike detected! Current: {current_count}, "
				f"Baseline: {baseline_count:.1f}, "
				f"Threshold: {threshold:.1f}"
			)
		
		return is_spike
	
	async def _get_baseline_content_count(
		self,
		scenario: BotScenario,
		hours: int
	) -> float:
		"""
		Get average content count from historical analytics.
		
		Args:
			scenario: Bot scenario
			hours: Lookback period in hours
		
		Returns:
			Average content count per collection cycle
		"""
		try:
			# Get analytics from last N hours
			since = datetime.now(timezone.utc) - timedelta(hours=hours)
			
			# Query analytics for sources using this scenario
			# Note: This requires source_id in AIAnalytics
			# For now, approximate with all recent analytics
			analytics = await AIAnalytics.objects.filter(
				created_at__gte=since
			)
			
			if not analytics:
				return 0.0
			
			# Calculate average content count
			# Assuming analytics has content_count or similar field
			# If not available, use number of analytics entries as proxy
			total_count = len(analytics)
			avg_count = total_count / max(1, hours / scenario.collection_interval_hours)
			
			return avg_count
			
		except Exception as e:
			logger.error(f"Failed to get baseline: {e}")
			return 0.0
	
	# ============================================================================
	# Post-analysis filters
	# ============================================================================
	
	async def _check_sentiment_threshold(
		self,
		analysis_result: dict,
		config: dict
	) -> bool:
		"""
		Check if sentiment meets threshold condition.
		
		Config example:
		{
			"threshold": 0.3,
			"direction": "below"  # "below" or "above"
		}
		"""
		threshold = config.get("threshold", 0.5)
		direction = config.get("direction", "below")
		
		# Extract sentiment from analysis result
		# The structure depends on your analyzer implementation
		sentiment = analysis_result.get("sentiment", {})
		
		# Try different possible keys
		score = (
			sentiment.get("score") or
			sentiment.get("overall_score") or
			sentiment.get("average_score") or
			0.5  # Default neutral
		)
		
		if direction == "below":
			return score < threshold
		elif direction == "above":
			return score > threshold
		else:
			return True  # Unknown direction = allow


# Singleton instance
trigger_evaluator = TriggerEvaluator()
