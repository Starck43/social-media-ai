"""
LLM optimization utilities for cost reduction and rate limiting.

Provides:
— Rate limiting to avoid API throttling
— Cost tracking for budget monitoring
— Batch optimization for multiple items
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
	from app.models import LLMProvider

logger = logging.getLogger(__name__)


class LLMOptimizer:
	"""
	LLM optimization suite — rate limiting, cost tracking, batching.
	
	Coordinates multiple optimization strategies to reduce costs
	and prevent API throttling while maintaining quality.
	"""

	def __init__(self):
		self.rate_limiter = LLMRateLimiter()
		self.cost_tracker = CostTracker()
		self.batch_optimizer = BatchOptimizer()

	async def optimize_request(
			self,
			items: list[dict],
			provider: 'LLMProvider'
	) -> tuple[list[dict], float]:
		"""
		Optimize LLM request with rate limiting, batching, and cost tracking.

		Args:
			items: List of content items to analyze
			provider: LLM provider to use
			
		Returns:
			Tuple of (results, cost_usd)
		"""

		# 1. Check rate limit
		await self.rate_limiter.acquire(provider.provider_type.value)

		# 2. Batch optimization (if supported)
		# Note: Most vision models don't support batching
		if len(items) > 1 and self._supports_batch(items, provider):
			results = await self.batch_optimizer.batch_analyze(items, provider)
		else:
			# Analyze individually
			results = []
			for item in items:
				result = await self._analyze_single(item, provider)
				results.append(result)

		# 3. Track cost
		total_input_tokens = sum(r.get("input_tokens", 0) for r in results)
		total_output_tokens = sum(r.get("output_tokens", 0) for r in results)

		cost = await self.cost_tracker.track_usage(
			provider.provider_type.value,
			input_tokens=total_input_tokens,
			output_tokens=total_output_tokens
		)

		return results, cost

	def _supports_batch(self, items: list[dict], provider: 'LLMProvider') -> bool:
		"""
		Check if batching is supported for these items.
		
		Batching only works for:
		— Text-only content
		— Same provider/model
		— Provider explicitly supports it
		"""
		# Only batch text items
		if any(item.get("type") != "text" for item in items):
			return False

		# Check provider capabilities
		return "text" in provider.capabilities

	async def _analyze_single(self, item: dict, provider: 'LLMProvider') -> dict:
		"""
		Analyze single item (placeholder — implement in an analyzer).
		
		This is a placeholder that should be replaced with actual
		LLM API call in the analyzer service.
		"""
		# This will be implemented by analyzer_v2
		return {
			"content": item,
			"input_tokens": 0,
			"output_tokens": 0,
			"result": None
		}


class LLMRateLimiter:
	"""
	Rate limiter to prevent API throttling.
	
	Tracks requests per a provider to avoid exceeding rate limits.
	Different providers have different limits:
	— OpenAI: 60 RPM (tier 1), 3500 RPM (tier 3)
	— Anthropic: 50 RPM (tier 1)
	— DeepSeek: 60 RPM
	"""

	# Default rate limits by provider
	DEFAULT_LIMITS = {
		"openai": 60,
		"anthropic": 50,
		"deepseek": 60,
		"google": 60,
	}

	def __init__(self, custom_limits: Optional[dict[str, int]] = None):
		"""
		Initialize rate limiter.
		
		Args:
			custom_limits: Optional dict of provider -> RPM limits
		"""
		self.limits = {**self.DEFAULT_LIMITS, **(custom_limits or {})}
		# Track requests per provider
		self.requests: dict[str, list[datetime]] = defaultdict(list)

	async def acquire(self, provider: str):
		"""
		Wait if rate limit would be exceeded.
		
		Args:
			provider: Provider type (openai, deepseek, etc)
		"""
		now = datetime.now()
		provider_requests = self.requests[provider]

		# Remove old requests (older than 1 minute)
		self.requests[provider] = [
			r for r in provider_requests
			if now - r < timedelta(minutes=1)
		]

		# Check if we would exceed limit
		max_rpm = self.limits.get(provider, 60)
		if len(self.requests[provider]) >= max_rpm:
			# Wait until oldest request expires
			oldest = self.requests[provider][0]
			wait_time = 60 - (now - oldest).total_seconds()
			if wait_time > 0:
				logger.warning(
					f"Rate limit reached for {provider} ({max_rpm} RPM), "
					f"waiting {wait_time:.1f}s"
				)
				await asyncio.sleep(wait_time)

		# Record this request
		self.requests[provider].append(datetime.now())


class CostTracker:
	"""
	Cost tracking for LLM API usage.
	
	Tracks token usage and calculates costs based on provider pricing.
	Helps monitor spending and optimize budget allocation.
	"""

	# Pricing per 1M tokens (USD)
	PRICING = {
		"deepseek": {"input": 0.14, "output": 0.28},
		"openai": {"input": 10.0, "output": 30.0},  # GPT-4 Vision
		"anthropic": {"input": 15.0, "output": 75.0},  # Claude Opus
		"google": {"input": 7.0, "output": 21.0},  # Gemini Pro
	}

	def __init__(self):
		"""Initialize cost tracker."""
		# In-memory accumulator for current session
		self.session_costs: dict[str, float] = defaultdict(float)
		self.session_tokens: dict[str, dict[str, int]] = defaultdict(
			lambda: {"input": 0, "output": 0}
		)

	async def track_usage(
			self,
			provider: str,
			input_tokens: int,
			output_tokens: int
	) -> float:
		"""
		Calculate and track cost for API usage.
		
		Args:
			provider: Provider type
			input_tokens: Number of input tokens
			output_tokens: Number of output tokens
			
		Returns:
			Cost in USD
		"""
		# Get pricing for provider
		pricing = self.PRICING.get(provider)
		if not pricing:
			logger.warning(f"No pricing data for provider: {provider}")
			return 0.0

		# Calculate cost
		cost = (
				input_tokens / 1_000_000 * pricing["input"] +
				output_tokens / 1_000_000 * pricing["output"]
		)

		# Track in session
		self.session_costs[provider] += cost
		self.session_tokens[provider]["input"] += input_tokens
		self.session_tokens[provider]["output"] += output_tokens

		# Log for monitoring
		logger.info(
			f"LLM usage: {provider} | "
			f"tokens: {input_tokens}↑ {output_tokens}↓ | "
			f"cost: ${cost:.4f}"
		)

		# TODO: Save to database for persistent tracking
		# This would require a UsageLog model
		# await self._save_to_db(provider, input_tokens, output_tokens, cost)

		return cost

	def get_session_summary(self) -> dict[str, Any]:
		"""
		Get summary of costs for current session.
		
		Returns:
			Dict with total costs and token counts per provider
		"""
		return {
			"total_cost": sum(self.session_costs.values()),
			"by_provider": {
				provider: {
					"cost": self.session_costs[provider],
					"tokens": self.session_tokens[provider]
				}
				for provider in self.session_costs.keys()
			}
		}


class BatchOptimizer:
	"""
	Batch optimization for multiple content items.
	
	Combines multiple text items into single LLM request to reduce:
	— API calls (saves time)
	— Total tokens (saves money)
	— Rate limit usage
	
	Note: Only works for text content, vision models process individually.
	"""

	MAX_BATCH_SIZE = 20  # Max items per batch
	MAX_BATCH_TOKENS = 3000  # Approximate token limit per batch

	async def batch_analyze(
			self,
			items: list[dict],
			provider: 'LLMProvider'
	) -> list[dict]:
		"""
		Analyze multiple items in one LLM request.
		
		Args:
			items: List of content items
			provider: LLM provider to use
			
		Returns:
			List of analysis results
		"""
		# Filter text-only items
		text_items = [i for i in items if i.get("type") == "text"]

		if not text_items:
			return []

		# Split into batches
		batches = self._create_batches(text_items)

		# Process each batch
		all_results = []
		for batch in batches:
			prompt = self._create_batch_prompt(batch)

			# This is a placeholder — actual call should be in analyzer
			result = {
				"batch_size": len(batch),
				"prompt": prompt,
				"input_tokens": len(prompt.split()) * 1.3,  # Rough estimate
				"output_tokens": len(batch) * 100,  # Rough estimate
			}

			all_results.append(result)

		return all_results

	def _create_batches(self, items: list[dict]) -> list[list[dict]]:
		"""
		Split items into optimal batches.
		
		Considers:
		— Max batch size
		— Estimated token count
		"""
		batches = []
		current_batch = []
		current_tokens = 0

		for item in items:
			# Rough token estimate (1 token ≈ 0.75 words)
			item_tokens = len(str(item.get("text", "")).split()) * 1.3

			# Check if adding this item would exceed limits
			if len(current_batch) >= self.MAX_BATCH_SIZE or current_tokens + item_tokens > self.MAX_BATCH_TOKENS:

				if current_batch:
					batches.append(current_batch)
					current_batch = []
					current_tokens = 0

			current_batch.append(item)
			current_tokens += item_tokens

		# Add final batch
		if current_batch:
			batches.append(current_batch)

		return batches

	def _create_batch_prompt(self, items: list[dict]) -> str:
		"""
		Create prompt for multiple posts.
		
		Args:
			items: List of content items to analyze together
			
		Returns:
			Combined prompt for batch analysis
		"""
		posts = "\n\n".join([
			f"POST {i + 1}:\n{item.get('text', '')}"
			for i, item in enumerate(items)
		])

		return (f"""
			Analyze the following {len(items)} social media posts.
				For each post provide:
				— Sentiment (positive/negative/neutral)
				— Main topics (up to 3)
				— Key entities (people, organizations, locations)
				
				{posts}
				
				Respond in JSON format:
				[
					{{"post_id": 1, "sentiment": "...", "topics": [...], "entities": [...]}},
					{{"post_id": 2, "sentiment": "...", "topics": [...], "entities": [...]}}
				]
			""")
