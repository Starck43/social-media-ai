import logging
from typing import Dict, List, Any

from app.types.enums.llm_types import MediaType

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
		classified = {
			MediaType.TEXT.value: [],
			MediaType.IMAGE.value: [],
			MediaType.VIDEO.value: []
		}
		
		for item in content:
			# All items have text component
			if item.get('text'):
				classified[MediaType.TEXT.value].append(item)
			
			# Check for media attachments
			attachments = item.get('attachments', [])
			
			for attachment in attachments:
				media_type = attachment.get('type', '').lower()
				
				if media_type in ['photo', 'image']:
					classified[MediaType.IMAGE.value].append({
						**item,
						'media_url': attachment.get('url'),
						'media_type': MediaType.IMAGE.value
					})
				elif media_type in ['video', 'video_file']:
					classified[MediaType.VIDEO.value].append({
						**item,
						'media_url': attachment.get('url'),
						'media_type': MediaType.VIDEO.value
					})
		
		logger.info(
			f"Classified content: {len(classified[MediaType.TEXT.value])} text items, "
			f"{len(classified[MediaType.IMAGE.value])} images, {len(classified[MediaType.VIDEO.value])} videos"
		)
		
		return classified
	
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
	def prepare_text_content(items: list[dict[str, Any]], sample_size: int = 100) -> str:
		"""
		Prepare text content for LLM analysis with sampling.
		
		Args:
			items: List of text content items
			sample_size: Maximum number of items to include
			
		Returns:
			Formatted text string
		"""
		texts = []
		step = max(1, len(items) // sample_size)
		
		for i in range(0, len(items), step):
			if len(texts) >= sample_size:
				break
			text = items[i].get("text", "")
			if text and len(text.strip()) > 10:
				date = items[i].get('date', '')
				texts.append(f"[{date}] {text}")
		
		return "\n\n".join(texts[:sample_size])
