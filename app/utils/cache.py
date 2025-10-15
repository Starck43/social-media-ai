"""
Short-term caching utilities for LLM requests.

Uses in-memory LRU cache for retry logic only.
Does NOT cache analysis results (they are stored in DB).
"""
import asyncio
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from hashlib import md5
from typing import Optional, Any

logger = logging.getLogger(__name__)


@lru_cache(maxsize=100)
def _cache_key(prompt: str) -> str:
	"""
	Generate cache key for retry logic.
	
	Args:
		prompt: LLM prompt text
		
	Returns:
		MD5 hash of prompt
	"""
	return md5(prompt.encode()).hexdigest()


class RetryCache:
	"""
	A short-term cache for retry logic.
	
	Prevents double-charging when retrying failed requests.
	Cache is memory-only (no Redis) with 5 minute TTL.
	"""

	def __init__(self, ttl_seconds: int = 300):
		"""
		Initialize retry cache.
		
		Args:
			ttl_seconds: Time to live for cached items (default 5 min)
		"""
		self.cache: dict[str, tuple[Any, datetime]] = {}
		self.ttl = timedelta(seconds=ttl_seconds)

	def get(self, key: str) -> Optional[Any]:
		"""
		Get cached value if not expired.
		
		Args:
			key: Cache key
			
		Returns:
			Cached value or None if expired/missing
		"""
		if key not in self.cache:
			return None

		value, timestamp = self.cache[key]

		# Check if expired
		if datetime.now() - timestamp > self.ttl:
			del self.cache[key]
			return None

		return value

	def set(self, key: str, value: Any):
		"""
		Cache value with a current timestamp.
		
		Args:
			key: Cache key
			value: Value to cache
		"""
		self.cache[key] = (value, datetime.now())

	def clear_expired(self):
		"""Remove expired entries from a cache."""
		now = datetime.now()
		expired = [
			k for k, (_, ts) in self.cache.items()
			if now - ts > self.ttl
		]
		for key in expired:
			del self.cache[key]


# Global retry cache instance
_retry_cache = RetryCache(ttl_seconds=300)


async def call_with_retry(
		prompt: str,
		call_func,
		max_retries: int = 3,
		backoff_base: float = 2.0
) -> Any:
	"""
	Call LLM with an automatic retry and short-term caching.
	
	Caches responses for 5 minutes to prevent double-charging
	when retrying rate-limited requests.
	
	Args:
		prompt: LLM prompt
		call_func: Async function to call LLM API
		max_retries: Maximum retry attempts
		backoff_base: Exponential backoff base (seconds)
		
	Returns:
		LLM response
		
	Raises:
		Last exception if all retries fail
	"""
	cache_key = _cache_key(prompt)

	# Check cache first
	cached = _retry_cache.get(cache_key)
	if cached is not None:
		logger.info(f"Using cached response for prompt hash {cache_key[:8]}")
		return cached

	# Try calling with retries
	last_exception = None
	for attempt in range(max_retries):
		try:
			result = await call_func(prompt)

			# Cache successful response
			_retry_cache.set(cache_key, result)

			return result

		except Exception as e:
			last_exception = e

			# Check if retryable
			if not _is_retryable(e):
				raise

			# Calculate backoff
			if attempt < max_retries - 1:
				wait_time = backoff_base ** attempt
				logger.warning(
					f"Attempt {attempt + 1} failed: {e}. "
					f"Retrying in {wait_time}s..."
				)
				await asyncio.sleep(wait_time)

	# All retries failed
	raise last_exception


def _is_retryable(exception: Exception) -> bool:
	"""
	Check if exception is retryable.
	
	Retryable errors:
	— Rate limiting (429)
	— Temporary server errors (5xx)
	— Network timeouts
	
	Non-retryable:
	— Invalid API key (401)
	— Bad request (400)
	— Content policy violation (400)
	"""
	error_msg = str(exception).lower()

	# Retryable patterns
	retryable = [
		"rate limit",
		"429",
		"too many requests",
		"timeout",
		"500",
		"502",
		"503",
		"504",
	]

	return any(pattern in error_msg for pattern in retryable)
