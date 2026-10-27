import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from app.core.config import settings
from app.models import Source
from app.types import SourceType

logger = logging.getLogger(__name__)


class BaseClient(ABC):
	"""Base class for all social media platform clients"""

	def __init__(self, platform):
		self.platform = platform

	async def collect_data(self, source: Source, content_type: str = "posts") -> list[dict]:
		"""
		Unified collection - supports both full (CLI) and incremental (background) modes

		Args:
			source: Data source with collection parameters
			content_type: Type of content to collect

		Returns:
			list[dict]: Normalized content items
		"""
		try:
			method = self._get_api_method(source.source_type, content_type)

			# Determine collection mode
			is_incremental = source.params.get('incremental_mode', False)

			if is_incremental:
				# Background task mode - incremental collection with checkpoint
				return await self._collect_incremental(source, method, content_type)
			else:
				# CLI mode - full collection with internal pagination
				return await self._collect_full(source, method, content_type)

		except Exception as e:
			logger.error(f"Data collection error for {source.name}: {e}")
			raise

	async def _collect_full(self, source: Source, method: str, content_type: str) -> list[dict]:
		"""
		Full collection for CLI - gets all available content with internal pagination
		"""
		all_items = []
		offset = 0
		page_size = settings.SOCIAL_PAGE_SIZE
		max_pages = settings.SOCIAL_MAX_PAGES
		# A bounded slice, not "everything the platform has". A date typo or a
		# first run without a lower bound must not park an archive in
		# `collected_items`; the operator can lower the cap per source, never
		# raise it above the deployment's hard ceiling.
		requested_limit = (source.params.get("collection") or {}).get("limit") or source.params.get("limit")
		try:
			collect_limit = int(requested_limit) if requested_limit else settings.SOCIAL_COLLECT_LIMIT
		except (TypeError, ValueError):
			collect_limit = settings.SOCIAL_COLLECT_LIMIT
		collect_limit = max(1, min(collect_limit, settings.SOCIAL_COLLECT_HARD_LIMIT))

		# Get date filters from source. `wall.get` does not accept
		# start_time/end_time (VK ignores them), so the window is enforced here
		# after each page — not only for a forced refresh. Without this a task's
		# configured start date let older posts through.
		cli_dates = source.params.get('cli_dates', {}) if source.params else {}
		start_date = cli_dates.get('start_date')
		end_date = cli_dates.get('end_date')

		pages_collected = 0

		for page in range(max_pages):
			params = self._build_params(source, method)
			params['offset'] = offset
			params['count'] = page_size

			logger.info(f"Fetching page {page + 1}, offset: {offset}")

			res = await self._fetch_page(method, params)
			raw_items = self._extract_items_from_response(res)

			if not raw_items:
				break

			items = raw_items
			if start_date:
				filtered_items = self._filter_items_by_date(items, start_date)
				logger.info(f"Date filtering: {len(items)} -> {len(filtered_items)} items after {start_date}")
				if not filtered_items:
					# Newest-first pages: once a whole page is older than the
					# window, every later page is too.
					break
				items = filtered_items
			if end_date:
				items = self._filter_items_before_date(items, end_date)

			all_items.extend(items)
			pages_collected += 1

			# The cap is a run ceiling, not a page size: stop as soon as the
			# slice is full and keep exactly the newest-first prefix.
			if len(all_items) >= collect_limit:
				all_items = all_items[:collect_limit]
				break

			# Check if we should stop pagination. Raw page size decides it, not
			# the filtered count: a page that lost old posts still proves there
			# are more pages behind it.
			if self._should_stop_pagination(res, raw_items, page_size, len(all_items)):
				break

			offset += page_size
			await asyncio.sleep(0.1)

		normalized_data = self._normalize_response(
			self._build_paginated_response(all_items),
			source.source_type
		)

		logger.info(f"Full collection: {len(all_items)} items from {pages_collected} pages")
		return normalized_data or []

	async def _collect_incremental(self, source: Source, method: str, content_type: str) -> list[dict]:
		"""
		Incremental collection for background tasks - single page with checkpoint
		"""
		from app.services.checkpoint_manager import CheckpointManager

		# Get checkpoint parameters
		checkpoint_params = CheckpointManager.get_collection_params(source)

		# Build request with checkpoint data
		params = self._build_params(source, method)
		params.update(checkpoint_params)

		logger.info(f"Incremental collection with params: {list(checkpoint_params.keys())}")

		# Collect single page
		res = await self._fetch_page(method, params)
		items = self._extract_items_from_response(res)

		if not items:
			logger.info("No new content in incremental collection")
			return []

		normalized_data = self._normalize_response(res, source.source_type)

		logger.info(f"Incremental collection: {len(items)} new items")
		return normalized_data or []

	def _filter_items_by_date(self, items: list, start_date) -> list:
		"""Filter items by start date on client side with timezone awareness."""
		from app.utils.date_parsing import universal_date_parser
		from datetime import datetime, timezone

		start_dt = universal_date_parser(start_date)
		if not start_dt:
			return items

		if start_dt.tzinfo is None:
			start_dt = start_dt.replace(tzinfo=timezone.utc)

		filtered = []
		for item in items:
			item_date = item.get('date')
			if not item_date:
				continue

			# Convert timestamp to datetime if needed
			if isinstance(item_date, (int, float)):
				# VK timestamp is UTC
				item_date = datetime.fromtimestamp(item_date, tz=timezone.utc)
			elif isinstance(item_date, datetime):
				if item_date.tzinfo is None:
					item_date = item_date.replace(tzinfo=timezone.utc)

			if item_date >= start_dt:
				filtered.append(item)

		logger.info(f"Date filtering: {len(items)} -> {len(filtered)} items after {start_dt}")
		return filtered

	def _filter_items_before_date(self, items: list, end_date) -> list:
		"""Drop items published after the window's end (inclusive end-of-day)."""
		from datetime import datetime, timezone

		from app.utils.date_parsing import universal_date_parser

		end_dt = universal_date_parser(end_date)
		if not end_dt:
			return items
		if end_dt.tzinfo is None:
			end_dt = end_dt.replace(tzinfo=timezone.utc)
		# A date-only end means "through that day", not midnight at its start.
		if getattr(end_date, "hour", None) is None and getattr(end_date, "minute", None) is None:
			end_dt = end_dt.replace(hour=23, minute=59, second=59)

		filtered = []
		for item in items:
			item_date = item.get("date")
			if not item_date:
				continue
			if isinstance(item_date, (int, float)):
				item_date = datetime.fromtimestamp(item_date, tz=timezone.utc)
			elif isinstance(item_date, datetime) and item_date.tzinfo is None:
				item_date = item_date.replace(tzinfo=timezone.utc)
			if item_date <= end_dt:
				filtered.append(item)
		return filtered

	async def _make_request(self, method: str, params: dict, *, form_data: bool = False) -> dict:
		"""
		Asynchronous HTTP request logic using httpx

		Args:
			method: API endpoint method
			params: Query parameters for the request

		Returns:
			dict: Parsed JSON response

		Raises:
			HTTPError: If the request fails
		"""
		api_base_url = (self.platform.params or {}).get('api_base_url')
		if not api_base_url:
			platform_type = getattr(self.platform, "platform_type", None)
			from app.utils.enum_helpers import get_enum_value
			pt_val = get_enum_value(platform_type)
			if pt_val == "vk":
				api_base_url = settings.VK_API_BASE_URL
			elif pt_val == "telegram":
				api_base_url = settings.TELEGRAM_API_BASE_URL
			else:
				api_base_url = ""

		timeout = getattr(settings, "VK_REQUEST_TIMEOUT", settings.SOCIAL_REQUEST_TIMEOUT)
		async with httpx.AsyncClient() as client:
			if form_data:
				# Credential-bearing operations keep secrets out of the request
				# URL and the HTTP client's standard request log messages.
				response = await client.post(f"{api_base_url}/{method}", data=params, timeout=timeout)
			else:
				response = await client.get(
					f"{api_base_url}/{method}",
					params=params,
					timeout=timeout
				)
			response.raise_for_status()
			return response.json()

	async def _fetch_page(self, method: str, params: dict) -> dict:
		"""One collection read with bounded rate-limit handling, then error check.

		A short platform limit (VK code 6, HTTP/Bot API 429 with a small
		retry hint) is waited out and retried within the configured budget. A
		limit that does not clear, or cannot clear within a run, raises
		`SourceRateLimited` so the source is deferred instead of failing or
		reading as empty. Other API errors keep `_raise_api_error` semantics.
		"""
		from app.services.social.rate_limit import (
			SourceRateLimited,
			http_rate_limit,
			next_pause,
			response_rate_limit,
		)

		attempt = 0
		while True:
			try:
				res = await self._make_request(method, params)
			except httpx.HTTPStatusError as error:
				limit = http_rate_limit(error)
				if limit is None:
					raise
			else:
				limit = response_rate_limit(res)
				if limit is None:
					self._raise_api_error(res)
					return res
			retryable, retry_after = limit
			pause = next_pause(attempt, retryable, retry_after)
			platform = self._platform_name()
			if pause is None:
				logger.warning(
					"social_rate_limit_deferred platform=%s method=%s attempts=%s", platform, method, attempt + 1
				)
				raise SourceRateLimited(platform, retry_after)
			logger.info("social_rate_limited platform=%s method=%s pause=%.1fs", platform, method, pause)
			await asyncio.sleep(pause)
			attempt += 1

	def _platform_name(self) -> str:
		from app.utils.enum_helpers import get_enum_value

		value = get_enum_value(getattr(self.platform, "platform_type", None))
		return value if isinstance(value, str) and value else "unknown"

	def _raise_api_error(self, response: dict) -> None:
		"""
		Raise a clear (Russian) exception when the platform returns an API error.

		VK-style responses carry the error under a top-level `error` key
		(`{"error": {"error_code": 15, "error_msg": "..."}}`). Without this check
		such an error is silently treated as an empty result, which hides real
		problems (private wall, missing permission, quota, auth failure).
		"""
		error = response.get("error") if isinstance(response, dict) else None
		if not isinstance(error, dict):
			return
		code = error.get("error_code") or error.get("code")
		msg = error.get("error_msg") or error.get("message") or "неизвестная ошибка API"
		raise RuntimeError(f"Ошибка API ({code or 'без кода'}): {msg}")

	@staticmethod
	def _convert_to_datetime(date_obj, end_of_day=False):
		"""
		Convert various date formats to datetime with timezone.
		Uses universal date parser with end_of_day enhancement.

		Args:
			date_obj: Can be datetime, date, string or timestamp
			end_of_day: If True, set time to 23:59:59 for end dates

		Returns:
			datetime object with timezone or None if conversion fails
		"""
		from app.utils.date_parsing import universal_date_parser

		# Use universal parser for base conversion
		result = universal_date_parser(date_obj)

		# Apply end_of_day adjustment if needed
		if result and end_of_day:
			result = result.replace(hour=23, minute=59, second=59)

		return result

	@abstractmethod
	def _get_api_method(self, source_type: SourceType, content_type: str) -> str:
		"""Returns API method for source type and content type"""
		pass

	@abstractmethod
	def _build_params(self, source: Source, method: str) -> dict:
		"""Builds parameters for API request"""
		pass

	@abstractmethod
	def _normalize_response(self, raw_data: dict, source_type: SourceType) -> list[dict]:
		"""Normalizes platform-specific data to unified format"""
		pass

	@abstractmethod
	def _extract_items_from_response(self, response: dict) -> list:
		"""Extract items from API response (override per platform)."""
		raise NotImplementedError("Platform clients must implement _extract_items_from_response")

	@abstractmethod
	def _should_stop_pagination(self, response: dict, items: list, page_size: int, total_collected: int) -> bool:
		"""Determine if pagination should stop."""
		raise NotImplementedError("Platform clients must implement _should_stop_pagination")


	@abstractmethod
	def _build_paginated_response(self, all_items: list) -> dict:
		"""Build response structure for normalization."""
		raise NotImplementedError("Platform clients must implement _build_paginated_response")
