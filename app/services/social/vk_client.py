"""
VK API client for collecting posts, comments, and other content.

An official documentation: https://dev.vk.com/ru/reference
"""

import logging
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.models import Source
from app.services.social.base import BaseClient
from app.types import SourceType
from app.utils.enum_helpers import get_enum_value

logger = logging.getLogger(__name__)


class VKClient(BaseClient):
	"""
	Client for VK API integration.

	Supports:
	— Wall posts collection (wall.get)
	— Comments collection (wall.getComments)
	— User/Group information (users.get, groups.getById)
	— Messages for authorized groups (messages.getHistory)
	"""

	def _get_api_method(self, source_type: SourceType, content_type: str) -> str:
		"""
		Get VK API method based on source type and content type.

		Args:
			source_type: Type of source (USER, GROUP, CHANNEL, PUBLIC, etc.)
			content_type: Type of content to collect (posts, comments, photos, etc.)

		Returns:
			VK API method name
		"""
		# Group-like methods (same API for all community types)
		group_methods = {
			"posts": "wall.get",
			"comments": "wall.getComments",
			"info": "groups.getById",
		}
		
		methods = {
			SourceType.USER: {
				"posts": "wall.get",
				"comments": "wall.getComments",
				"info": "users.get",
			},
			SourceType.GROUP: group_methods,
			SourceType.CHANNEL: group_methods,
			SourceType.PUBLIC: group_methods,
			SourceType.PAGE: group_methods,
			SourceType.EVENT: group_methods,
			SourceType.MARKET: {
				"posts": "wall.get",
				"comments": "wall.getComments",
				"info": "groups.getById",
				"products": "market.get",
				"product_info": "market.getById",
			},
			SourceType.ALBUM: {
				"photos": "photos.get",
				"info": "photos.getAlbums",
			},
			SourceType.CHAT: {
				"messages": "messages.getHistory",
				"info": "messages.getConversationById",
			},
		}

		return methods.get(source_type, {}).get(content_type, "wall.get")

	async def _resolve_external_id(self, external_id: str) -> str:
		"""
		Convert screen_name to numeric ID if needed.
		
		Args:
			external_id: Either numeric ID or screen_name
			
		Returns:
			str: Numeric ID (with minus for groups)
		"""
		# If already numeric (or negative for groups)
		if external_id.lstrip('-').isdigit():
			return external_id
		
		# Resolve screen_name via VK API
		logger.info(f"Resolving screen_name: {external_id}")
		url = f"{self.platform.params.get('api_base_url', 'https://api.vk.com/method')}/utils.resolveScreenName"
		params = {
			"screen_name": external_id,
			"access_token": settings.VK_SERVICE_ACCESS_TOKEN,
			"v": "5.199"
		}
		
		import httpx
		async with httpx.AsyncClient() as client:
			response = await client.get(url, params=params)
			data = response.json()
			
			if 'response' in data and data['response']:
				object_id = data['response']['object_id']
				object_type = data['response']['type']
				
				# For groups/pages/events, add minus prefix
				if object_type in ['group', 'page', 'event']:
					resolved_id = f"-{object_id}"
				else:
					resolved_id = str(object_id)
				
				logger.info(f"Resolved {external_id} → {resolved_id} (type: {object_type})")
				return resolved_id
			
			logger.warning(f"Cannot resolve screen_name: {external_id}, using as-is")
			return external_id

	def _build_params(self, source: Source, method: str) -> dict:
		"""
		Build VK API request parameters.

		Constructs parameters according to VK API requirements:
		— owner_id: User ID (positive) or Group ID (negative)
		— domain: Short name of user/group
		— count: Amount posts (max 100)
		— offset: Pagination offset
		— extended: Include additional fields (0 or 1)
		— filter: Type of posts (all, owner, others, suggests)

		Args:
			source: Source object with external_id and params
			method: VK API method name

		Returns:
			Dictionary with request parameters
		"""
		platform_params = self.platform.params or {}
		source_params = source.params.get('collection', {}) if source.params else {}

		# Base parameters for all requests
		base_params = {
			'access_token': settings.VK_SERVICE_ACCESS_TOKEN,
			'v': platform_params.get('api_version', '5.199'),  # Latest API version
		}

		# Method-specific parameters
		if method in ('wall.get', 'wall.getComments'):
			# Parse owner_id from external_id
			owner_id = self._parse_owner_id(source.external_id, source.source_type)

			params_dict = {
				'owner_id': owner_id,
				'count': source_params.get('count', 100),  # Max 100 posts
				'offset': source_params.get('offset', 0),
				'extended': source_params.get('extended', 1),  # Include additional fields
				'filter': source_params.get('filter', 'all'),  # all, owner, others, suggests
			}
			
			# DATE RANGE FILTERING
			# Priority: source.date_from/date_to (model fields) > params > last_checked (checkpoint) > no filter
			
			# Get date_from from model fields first, then fallback to params
			date_from = source.date_from if hasattr(source, 'date_from') and source.date_from else source_params.get('date_from')
			date_to = source.date_to if hasattr(source, 'date_to') and source.date_to else source_params.get('date_to')
			
			# Parse and apply date_from (start boundary)
			if date_from:
				from datetime import datetime
				try:
					# Handle both string and datetime objects
					if isinstance(date_from, str):
						date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
					else:
						date_from_dt = date_from
					
					start_time = int(date_from_dt.timestamp())
					params_dict['start_time'] = start_time
					logger.info(f"VK date_from filter: {date_from_dt.isoformat()} (ts: {start_time})")
				except Exception as e:
					logger.warning(f"Failed to parse date_from '{date_from}': {e}")
			# Fallback to checkpoint if date_from not set
			elif source.last_checked:
				start_time = int(source.last_checked.timestamp())
				params_dict['start_time'] = start_time
				logger.info(f"VK checkpoint: collecting posts after {source.last_checked.isoformat()} (ts: {start_time})")
			
			# Parse and apply date_to (end boundary)
			if date_to:
				from datetime import datetime
				try:
					# Handle both string and datetime objects
					if isinstance(date_to, str):
						date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
					else:
						date_to_dt = date_to
					
					end_time = int(date_to_dt.timestamp())
					params_dict['end_time'] = end_time
					logger.info(f"VK date_to filter: {date_to_dt.isoformat()} (ts: {end_time})")
				except Exception as e:
					logger.warning(f"Failed to parse date_to '{date_to}': {e}")
			
			base_params.update(params_dict)

		elif method == 'users.get':
			# User info request
			user_ids = self._parse_owner_id(source.external_id, SourceType.USER)
			base_params.update({
				'user_ids': abs(int(user_ids)),
				'fields': 'photo_max,city,verified,followers_count',
			})

		elif method == 'groups.getById':
			# Group info request
			group_id = abs(int(self._parse_owner_id(source.external_id, SourceType.GROUP)))
			base_params.update({
				'group_id': group_id,
				'fields': 'description,members_count,activity,verified',
			})
			
		elif method == 'market.get':
			# Market products request
			owner_id = self._parse_owner_id(source.external_id, source.source_type)
			base_params.update({
				'owner_id': owner_id,
				'count': source_params.get('count', 100),
				'offset': source_params.get('offset', 0),
				'extended': source_params.get('extended', 1),
			})
			
		elif method == 'photos.get':
			# Album photos request
			owner_id = self._parse_owner_id(source.external_id, source.source_type)
			base_params.update({
				'owner_id': owner_id,
				'album_id': source_params.get('album_id', 'wall'),
				'count': source_params.get('count', 100),
				'offset': source_params.get('offset', 0),
				'extended': source_params.get('extended', 1),
			})
			
		elif method == 'messages.getHistory':
			# Chat messages (requires proper access token)
			peer_id = source.external_id
			base_params.update({
				'peer_id': peer_id,
				'count': source_params.get('count', 100),
				'offset': source_params.get('offset', 0),
				'rev': source_params.get('reverse', 0),
			})

		# Merge with any custom source parameters
		return {**base_params, **source_params}

	def _parse_owner_id(self, external_id: str, source_type: SourceType) -> str:
		"""
		Parse VK owner_id from external_id.

		VK uses:
		— Positive IDs for users (e.g., “12345” or "id12345")
		— Negative IDs for groups (e.g., "-12345" or "club12345")

		Args:
			external_id: An external identifier from Source
			source_type: Type of source

		Returns:
			Formatted owner_id as string
		"""
		# Clean up common prefixes
		clean_id = external_id.strip()
		clean_id = clean_id.replace('id', '').replace('club', '').replace('public', '').replace('event', '')
		clean_id = clean_id.replace('-', '')

		# Ensure numeric
		try:
			numeric_id = int(clean_id)
		except ValueError:
			logger.warning(f"Invalid VK ID format: {external_id}, using as-is")
			return clean_id

		# Apply negative for all community types
		group_types = (
			SourceType.GROUP,
			SourceType.CHANNEL,
			SourceType.PUBLIC,
			SourceType.PAGE,
			SourceType.EVENT,
			SourceType.MARKET
		)
		
		if source_type in group_types:
			return str(-abs(numeric_id))
		else:
			return str(abs(numeric_id))

	def _normalize_response(self, raw_data: dict, source_type: SourceType) -> list[dict[str, Any]]:
		"""
		Normalize VK API response to unified format.

		Converts VK-specific fields to common format:
		— ID: Post/comment ID
		— text: Post text content
		— date: Publication datetime
		— reactions: Combined engagement metrics
		— comments: Comment count
		— shares: Repost count
		— views: View count

		Args:
			raw_data: Raw response from VK API
			source_type: Type of source

		Returns:
			List of normalized content items
		"""
		# Handle VK API response format
		if 'response' not in raw_data:
			logger.warning(f"Unexpected VK response format: {raw_data}")
			return []

		response = raw_data['response']
		items = response.get('items', [])

		if not items:
			logger.info("No items in VK response")
			return []

		normalized = []
		for item in items:
			try:
				# Extract engagement metrics
				likes = item.get('likes', {}).get('count', 0)
				comments = item.get('comments', {}).get('count', 0)
				reposts = item.get('reposts', {}).get('count', 0)
				views = item.get('views', {}).get('count', 0)

				# Build normalized item
				normalized_item = {
					'id': str(item.get('id', '')),
					'external_id': f"{item.get('owner_id', '')}_{item.get('id', '')}",
					'text': item.get('text', ''),
					'date': datetime.fromtimestamp(item.get('date', 0)),

					# Engagement metrics
					'likes': likes,
					'comments': comments,
					'shares': reposts,
					'views': views,
					'reactions': likes + comments + reposts,  # Combined metric

					# Metadata
					'source_type': get_enum_value(source_type),
					'platform': 'vkontakte',
					'post_type': item.get('post_type', 'post'),

					# Additional VK-specific fields
					'from_id': item.get('from_id'),
					'owner_id': item.get('owner_id'),
					'is_pinned': item.get('is_pinned', False),
					'marked_as_ads': item.get('marked_as_ads', False),
				}

				# Include attachments info if present
				if 'attachments' in item:
					attachment_types = [att.get('type') for att in item['attachments']]
					normalized_item['has_attachments'] = True
					normalized_item['attachment_types'] = attachment_types

				normalized.append(normalized_item)

			except Exception as e:
				logger.error(f"Error normalizing VK item: {e}", exc_info=True)
				continue

		logger.info(f"Normalized {len(normalized)} VK items from {len(items)} raw items")
		return normalized
