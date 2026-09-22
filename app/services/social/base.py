import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

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
			return []

	async def _collect_full(self, source: Source, method: str, content_type: str) -> list[dict]:
		"""
		Full collection for CLI - gets all available content with internal pagination
		"""
		all_items = []
		offset = 0
		page_size = 50
		max_pages = 20

		# Get date filters from source
		force_refresh = source.params.get('force_refresh', False)
		cli_dates = source.params.get('cli_dates', {}) if source.params else {}
		start_date = cli_dates.get('start_date') if force_refresh else None

		pages_collected = 0

		for page in range(max_pages):
			params = self._build_params(source, method)
			params['offset'] = offset
			params['count'] = page_size

			logger.info(f"Fetching page {page + 1}, offset: {offset}")

			res = await self._make_request(method, params)
			items = self._extract_items_from_response(res)

			if not items:
				break

			if start_date:
				filtered_items = self._filter_items_by_date(items, start_date)
				logger.info(f"Date filtering: {len(items)} -> {len(filtered_items)} items after {start_date}")
				items = filtered_items

			all_items.extend(items)
			pages_collected += 1

			# Check if we should stop pagination
			if self._should_stop_pagination(res, items, page_size, len(all_items)):
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
		res = await self._make_request(method, params)
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

	async def _make_request(self, method: str, params: dict) -> dict:
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
		api_base_url = self.platform.params.get('api_base_url')
		async with httpx.AsyncClient() as client:
			response = await client.get(
				f"{api_base_url}/{method}",
				params=params,
				timeout=30.0
			)
			response.raise_for_status()
			return response.json()

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
