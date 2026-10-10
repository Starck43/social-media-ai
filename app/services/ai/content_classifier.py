import logging
from typing import Dict, List, Any

from app.types.enums.llm_types import MediaType
from app.utils.content_attachments import attachment_coverage_reason, canonical_attachment_type

logger = logging.getLogger(__name__)


class ContentClassifier:
	"""
	Classifies and groups content by media type (text, image, video).
	
	Separates mixed content into categories for processing by appropriate LLM providers.
	"""
	
	@staticmethod
	def classify_content(content: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
		"""
		Classify content items by their media type.
		
		Args:
			content: List of normalized content items with potential attachments
			
		Returns:
			Dictionary with keys: MediaType values, each containing relevant items
		"""
		classified: dict[str, list[Any]] = {
			MediaType.TEXT.db_value: [],
			MediaType.IMAGE.db_value: [],
			MediaType.VIDEO.db_value: []
		}
		
		for item in content:
			# All items have text component
			if item.get('text'):
				classified[MediaType.TEXT.db_value].append(item)
			
			# Check for media attachments
			attachments = item.get('attachments') or []
			
			for attachment in attachments if type(attachments) is list else []:
				media_type = canonical_attachment_type(attachment.get('type')) if type(attachment) is dict else 'unknown'
				
				if media_type == 'image':
					classified[MediaType.IMAGE.db_value].append({
						**item,
						'media_url': attachment.get('url'),
						'media_type': MediaType.IMAGE.db_value
					})
				elif media_type == 'video':
					classified[MediaType.VIDEO.db_value].append({
						**item,
						'media_url': attachment.get('url'),
						'media_type': MediaType.VIDEO.db_value
					})
		
		logger.info(
			f"Classified content: {len(classified[MediaType.TEXT.db_value])} text items, "
			f"{len(classified[MediaType.IMAGE.db_value])} images, {len(classified[MediaType.VIDEO.db_value])} videos"
		)
		
		return classified
	
	@staticmethod
	def uncovered_attachment_items(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
		"""Parents with concrete unsupported/missing media cannot be retired.

		Missing/NULL is legacy unknown, not repaired or reinterpreted here.
		Explicit [] means no attachments. Malformed non-NULL collections are
		concrete unknown evidence, not silently empty. Preserve each parent once.
		"""
		uncovered = []
		for item in content:
			attachments = item.get("attachments")
			if attachments is None:
				continue
			if type(attachments) is not list or any(attachment_coverage_reason(entry) for entry in attachments):
				uncovered.append(item)
		return uncovered

	@staticmethod
	def get_media_urls(items: list[dict[str, Any]]) -> list[str]:
		"""
		Extract media URLs from classified items.
		
		Args:
			items: List of items with media_url field
			
		Returns:
			List of media URLs
		"""
		urls = []
		for item in items:
			url = item.get('media_url')
			if url:
				urls.append(url)
		
		return urls
	
	@staticmethod
	def select_text_content(items: list[dict[str, Any]], sample_size: int = 100) -> list[dict[str, Any]]:
		"""The exact bounded text sample used by prompts and coverage evidence."""
		selected = []
		step = max(1, len(items) // sample_size)
		for i in range(0, len(items), step):
			if len(selected) >= sample_size:
				break
			text = items[i].get("text", "")
			if text and len(text.strip()) > 10:
				selected.append(items[i])
		return selected

	@staticmethod
	def prepare_text_content(items: list[dict[str, Any]], sample_size: int = 100) -> str:
		"""Format the bounded sample without certifying omitted items as analyzed."""
		return "\n\n".join(
			f"[{item.get('date', '')}] {item['text']}"
			for item in ContentClassifier.select_text_content(items, sample_size)
		)
