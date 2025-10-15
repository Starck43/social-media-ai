"""
Example: Using checkpoint system with LLM optimizer.

Shows complete flow:
1. Check if collection needed
2. Collect only new content using checkpoint
3. Optimize LLM calls (rate limit + cost tracking + batch)
4. Analyze content
5. Save checkpoint
"""
import asyncio
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def example_complete_flow():
	"""Complete flow with checkpoint and optimizer."""
	from app.models import Source, LLMProvider
	from app.services.checkpoint_manager import CheckpointManager, CollectionResult
	from app.services.ai.optimizer import LLMOptimizer
	from app.services.ai.analyzer_v2 import AIAnalyzerV2
	
	# Example: Get source to monitor
	source = await Source.objects.get(id=1)
	logger.info(f"Checking source: {source.name}")
	
	# Step 1: Check if we should collect (based on checkpoint)
	should_collect = CheckpointManager.should_collect(
		source,
		collection_interval_minutes=60  # Collect every hour
	)
	
	if not should_collect:
		logger.info(
			f"Source {source.id} was checked recently "
			f"(last: {source.last_checked}), skipping"
		)
		return
	
	logger.info(f"Source {source.id} needs collection")
	
	# Step 2: Get checkpoint for incremental collection
	checkpoint = await CheckpointManager.get_checkpoint(source)
	last_checked = checkpoint["last_checked"]
	
	logger.info(f"Last checkpoint: {last_checked or 'Never'}")
	
	# Step 3: Collect only NEW content
	# Example for VK (pseudo-code - replace with actual collector)
	new_content = await collect_new_vk_posts(
		source=source,
		since=last_checked  # Only posts after this timestamp
	)
	
	if not new_content:
		logger.info(f"No new content for source {source.id}")
		
		# Update checkpoint anyway to avoid rechecking
		result = CollectionResult(
			source_id=source.id,
			content_count=0,
			has_new_content=False
		)
		await result.save_checkpoint()
		return
	
	logger.info(f"Collected {len(new_content)} new items")
	
	# Step 4: Analyze with LLM + Optimizer
	optimizer = LLMOptimizer()
	analyzer = AIAnalyzerV2()
	
	# Get provider for analysis
	provider = await LLMProvider.objects.filter(
		is_active=True,
		capabilities__contains=["text"]
	).first()
	
	if not provider:
		logger.error("No active LLM provider found")
		return
	
	# Optimize and analyze
	logger.info(f"Analyzing with {provider.name} (rate limited + batched)")
	
	results, cost = await optimizer.optimize_request(
		items=new_content,
		provider=provider
	)
	
	logger.info(f"Analysis complete. Cost: ${cost:.4f}")
	
	# Step 5: Save full analysis to DB
	analysis = await analyzer.analyze_content(
		content=new_content,
		source=source
	)
	
	if not analysis:
		logger.error("Analysis failed")
		return
	
	# Step 6: Save checkpoint on success
	result = CollectionResult(
		source_id=source.id,
		content_count=len(new_content),
		has_new_content=True,
		checkpoint_params={
			# Platform-specific cursors can be saved here
			"last_post_id": new_content[0].get("id") if new_content else None
		}
	)
	
	success = await result.save_checkpoint()
	
	if success:
		logger.info("✅ Checkpoint saved successfully")
	else:
		logger.warning("⚠️  Failed to save checkpoint")
	
	# Get cost summary
	cost_summary = optimizer.cost_tracker.get_session_summary()
	logger.info(f"Session cost summary: {cost_summary}")


async def collect_new_vk_posts(source, since):
	"""
	Example VK collector with checkpoint support.
	
	In real implementation, this would call VK API with timestamp filter.
	"""
	# Pseudo-code for VK API
	# import vk_api
	# 
	# vk_params = {
	#     "owner_id": f"-{source.external_id}",
	#     "count": 100,
	#     "start_time": int(since.timestamp()) if since else None
	# }
	# 
	# response = await vk_api.wall.get(**vk_params)
	# posts = response["items"]
	#
	# # Filter: only posts newer than checkpoint
	# if since:
	#     posts = [
	#         p for p in posts
	#         if datetime.fromtimestamp(p["date"], tz=timezone.utc) > since
	#     ]
	# 
	# return posts
	
	# Mock data for example
	return [
		{
			"id": 12345,
			"text": "New post content 1",
			"date": datetime.now(timezone.utc).timestamp(),
			"type": "text"
		},
		{
			"id": 12346,
			"text": "New post content 2",
			"date": datetime.now(timezone.utc).timestamp(),
			"type": "text"
		}
	]


async def example_batch_optimization():
	"""Example: Batch optimization for multiple text posts."""
	from app.services.ai.optimizer import BatchOptimizer
	
	# Simulate 50 text posts
	items = [
		{"id": i, "text": f"Post content {i}", "type": "text"}
		for i in range(50)
	]
	
	optimizer = BatchOptimizer()
	
	# Split into optimal batches
	batches = optimizer._create_batches(items)
	
	logger.info(f"Split {len(items)} items into {len(batches)} batches")
	for i, batch in enumerate(batches):
		logger.info(f"  Batch {i+1}: {len(batch)} items")
	
	# Each batch will be sent as single LLM request
	# Cost: ~3 requests instead of 50 = 94% savings!


async def example_rate_limiting():
	"""Example: Rate limiting to avoid 429 errors."""
	from app.services.ai.optimizer import LLMRateLimiter
	
	# Create rate limiter (60 RPM for OpenAI)
	limiter = LLMRateLimiter()
	
	# Simulate 100 requests
	logger.info("Simulating 100 requests with rate limiting...")
	
	for i in range(100):
		# Wait if rate limit would be exceeded
		await limiter.acquire("openai")
		
		# Make request (pseudo-code)
		# await openai.chat.completions.create(...)
		
		logger.info(f"Request {i+1}/100 sent")
	
	logger.info("All requests completed without 429 errors!")


async def example_cost_tracking():
	"""Example: Track LLM costs per provider."""
	from app.services.ai.optimizer import CostTracker
	
	tracker = CostTracker()
	
	# Track several API calls
	await tracker.track_usage(
		provider="deepseek",
		input_tokens=1500,
		output_tokens=500
	)
	
	await tracker.track_usage(
		provider="openai",
		input_tokens=2000,
		output_tokens=800
	)
	
	await tracker.track_usage(
		provider="deepseek",
		input_tokens=1000,
		output_tokens=300
	)
	
	# Get summary
	summary = tracker.get_session_summary()
	
	logger.info(f"Total cost: ${summary['total_cost']:.4f}")
	for provider, data in summary["by_provider"].items():
		logger.info(
			f"  {provider}: ${data['cost']:.4f} "
			f"({data['tokens']['input']}↑ + {data['tokens']['output']}↓ tokens)"
		)


async def example_retry_with_cache():
	"""Example: Retry failed requests with caching."""
	from app.utils.cache import call_with_retry
	
	async def flaky_api_call(prompt):
		"""Simulates API that fails sometimes."""
		import random
		if random.random() < 0.5:  # 50% failure rate
			raise Exception("Rate limit exceeded (429)")
		return {"response": "Success", "tokens": 100}
	
	prompt = "Analyze this social media post..."
	
	logger.info("Calling flaky API with retry logic...")
	
	try:
		result = await call_with_retry(
			prompt=prompt,
			call_func=flaky_api_call,
			max_retries=3
		)
		logger.info(f"Success: {result}")
	except Exception as e:
		logger.error(f"Failed after all retries: {e}")


if __name__ == "__main__":
	# Run examples
	logger.info("=" * 60)
	logger.info("CHECKPOINT SYSTEM EXAMPLES")
	logger.info("=" * 60)
	
	# Uncomment to run specific examples:
	
	# asyncio.run(example_complete_flow())
	# asyncio.run(example_batch_optimization())
	# asyncio.run(example_rate_limiting())
	# asyncio.run(example_cost_tracking())
	# asyncio.run(example_retry_with_cache())
