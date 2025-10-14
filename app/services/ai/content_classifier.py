import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class ContentClassifier:
	"""
	Classifies and groups content by media type (text, image, video).
	
	Separates mixed content into categories for processing by appropriate LLM providers.
	"""
	
	@staticmethod
	def classify_content(content: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
		"""
		Classify content items by their media type.
		
		Args:
			content: List of normalized content items with potential attachments
			
		Returns:
			Dictionary with keys: 'text', 'images', 'videos', each containing relevant items
		"""
		classified = {
			'text': [],
			'images': [],
			'videos': []
		}
		
		for item in content:
			# All items have text component
			if item.get('text'):
				classified['text'].append(item)
			
			# Check for media attachments
			attachments = item.get('attachments', [])
			
			for attachment in attachments:
				media_type = attachment.get('type', '').lower()
				
				if media_type in ['photo', 'image']:
					classified['images'].append({
						**item,
						'media_url': attachment.get('url'),
						'media_type': 'image'
					})
				elif media_type in ['video', 'video_file']:
					classified['videos'].append({
						**item,
						'media_url': attachment.get('url'),
						'media_type': 'video'
					})
		
		logger.info(
			f"Classified content: {len(classified['text'])} text items, "
			f"{len(classified['images'])} images, {len(classified['videos'])} videos"
		)
		
		return classified
	
	@staticmethod
	def get_media_urls(items: List[Dict[str, Any]]) -> List[str]:
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
	def prepare_text_content(items: List[Dict[str, Any]], sample_size: int = 100) -> str:
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
