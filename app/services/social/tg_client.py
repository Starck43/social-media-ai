"""
Telegram API client for collecting messages and channel posts.

Uses Telethon library for MTProto API access.
Official documentation: https://docs.telethon.dev/
"""

import logging
from datetime import datetime
from typing import List, Dict, Any

from app.models import Source
from app.services.social.base import BaseClient
from app.types.models import SourceType

logger = logging.getLogger(__name__)


class TelegramClient(BaseClient):
	"""
	Client for Telegram API integration using Telethon.
	
	Supports:
	- Channel posts collection
	- Group messages collection (if bot is member)
	- User info retrieval
	- Message history
	
	Note: Requires Telethon client configuration in platform params.
	"""

	def _get_api_method(self, source_type: SourceType, content_type: str) -> str:
		"""
		Get Telegram API method based on source type and content type.
		
		Args:
			source_type: Type of source (CHANNEL, GROUP, USER)
			content_type: Type of content to collect (posts, messages, etc.)
			
		Returns:
			Method identifier for Telethon
		"""
		methods = {
			SourceType.CHANNEL: {
				"posts": "get_messages",
				"info": "get_entity",
			},
			SourceType.GROUP: {
				"messages": "get_messages",
				"info": "get_entity",
			},
			SourceType.USER: {
				"messages": "get_messages",
				"info": "get_entity",
			},
		}
		
		return methods.get(source_type, {}).get(content_type, "get_messages")

	def _build_params(self, source: Source, method: str) -> dict:
		"""
		Build Telegram API request parameters for Telethon.
		
		Parameters:
		- entity: Chat ID, username, or channel link
		- limit: Number of messages to retrieve
		- offset_id: Message ID for pagination
		- min_id: Minimum message ID
		- max_id: Maximum message ID
		
		Args:
			source: Source object with external_id and params
			method: Telethon method name
			
		Returns:
			Dictionary with request parameters
		"""
		platform_params = self.platform.params or {}
		source_params = source.params.get('collection', {}) if source.params else {}

		base_params = {
			'entity': source.external_id,  # Can be username (@channel), chat_id, or link
		}
		
		# Method-specific parameters
		if method == 'get_messages':
			base_params.update({
				'limit': source_params.get('limit', 100),
				'offset_id': source_params.get('offset_id', 0),
				'reverse': source_params.get('reverse', False),
			})
		
		# Merge with custom source parameters
		return {**base_params, **source_params}

	def _normalize_response(self, raw_data: dict, source_type: SourceType) -> List[Dict[str, Any]]:
		"""
		Normalize Telegram API response to unified format.
		
		Converts Telethon message objects to common format:
		- id: Message ID
		- text: Message text content
		- date: Publication datetime
		- reactions: Reaction count
		- views: View count
		- forwards: Forward count
		
		Args:
			raw_data: Raw response from Telethon (list of messages)
			source_type: Type of source
			
		Returns:
			List of normalized content items
		"""
		# Telethon returns list of Message objects or dict representation
		messages = raw_data.get('messages', []) if isinstance(raw_data, dict) else raw_data
		
		if not messages:
			logger.info("No messages in Telegram response")
			return []

		normalized = []
		for msg in messages:
			try:
				# Handle both dict and object responses
				if hasattr(msg, 'to_dict'):
					msg = msg.to_dict()
				
				# Extract message content
				text = msg.get('message', '') or msg.get('text', '')
				
				# Extract engagement metrics
				views = msg.get('views', 0) or 0
				forwards = msg.get('forwards', 0) or 0
				
				# Reactions (if available)
				reactions_data = msg.get('reactions', {})
				reactions_count = 0
				if reactions_data and isinstance(reactions_data, dict):
					results = reactions_data.get('results', [])
					reactions_count = sum(r.get('count', 0) for r in results)
				
				# Build normalized item
				normalized_item = {
					'id': str(msg.get('id', '')),
					'external_id': f"{msg.get('peer_id', {})}_{msg.get('id', '')}",
					'text': text,
					'date': msg.get('date') if isinstance(msg.get('date'), datetime) else datetime.fromtimestamp(msg.get('date', 0)),
					
					# Engagement metrics
					'views': views,
					'forwards': forwards,
					'reactions': reactions_count,
					'replies': msg.get('replies', {}).get('replies', 0) if msg.get('replies') else 0,
					
					# Metadata
					'source_type': source_type.value if source_type else 'unknown',
					'platform': 'telegram',
					'message_type': 'post' if msg.get('post') else 'message',
					
					# Additional Telegram-specific fields
					'from_id': msg.get('from_id'),
					'peer_id': msg.get('peer_id'),
					'is_pinned': msg.get('pinned', False),
					'edit_date': msg.get('edit_date'),
				}
				
				# Include media info if present
				if msg.get('media'):
					normalized_item['has_media'] = True
					media = msg['media']
					if hasattr(media, '__class__'):
						normalized_item['media_type'] = media.__class__.__name__
					elif isinstance(media, dict):
						normalized_item['media_type'] = media.get('_', 'unknown')
				
				normalized.append(normalized_item)
				
			except Exception as e:
				logger.error(f"Error normalizing Telegram message: {e}", exc_info=True)
				continue

		logger.info(f"Normalized {len(normalized)} Telegram messages from {len(messages)} raw messages")
		return normalized
