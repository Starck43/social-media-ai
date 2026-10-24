import logging
from typing import Dict, List, Any

from app.types.enums.llm_types import MediaType
from app.utils.content_attachments import canonical_attachment_type

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
				# A staged attachment without a usable URL carries no analyzable
				# media body (collector found none — see VK raw-content audit
				# Q-2). Bucketing it as media would request media analysis of
				# nothing and then withhold the parent post from text coverage
				# forever, so such an attachment is not media for analysis.
				if not attachment.get('url'):
					continue
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
		"""Parents whose attachment collection is unreadable evidence.

		Owner-approved coverage semantics: an attachment without a usable URL
		(collector found none, VK raw-content audit Q-2) or of an unknown type
		is not analyzable media at all, so it neither reaches a media stage nor
		withholds its parent post from text coverage. Only a malformed
		non-NULL attachment collection stays concrete unknown evidence here;
		the runtime media stages add their own per-result gaps via
		`_uncovered_media_hashes`. Preserve each parent once.
		"""
		uncovered = []
		for item in content:
			attachments = item.get("attachments")
			if attachments is None or attachments == []:
				continue
			if type(attachments) is not list:
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
			item = items[i]
			text = item.get("text", "")
			parent_context = item.get("parent_context")
			is_contextual_comment = (
				str(item.get("content_type") or "").lower() == "comment"
				and isinstance(parent_context, dict)
				and bool(str(parent_context.get("text") or "").strip())
			)
			if text and (len(text.strip()) > 10 or (is_contextual_comment and text.strip())):
				selected.append(items[i])
		return selected

	@staticmethod
	def prepare_text_content(items: list[dict[str, Any]], sample_size: int = 100) -> str:
		"""Format the bounded sample without certifying omitted items as analyzed."""
		blocks = []
		for item in ContentClassifier.select_text_content(items, sample_size):
			parent = item.get("parent_context")
			if str(item.get("content_type") or "").lower() == "comment" and isinstance(parent, dict):
				parent_text = str(parent.get("text") or "").strip()
				if parent_text:
					parent_date = parent.get("date") or ""
					parent_id = parent.get("external_id") or ""
					platform = parent.get("platform") or item.get("platform") or ""
					blocks.append(f"[CONTEXT: parent {platform}:{parent_id} {parent_date}] {parent_text}")
			blocks.append(f"[{item.get('date', '')}] {item['text']}")
		return "\n\n".join(blocks)
