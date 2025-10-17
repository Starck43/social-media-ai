"""
Prompt variable substitution and helpers.

Provides utilities for:
- Variable substitution in custom prompts
- Available variable documentation
- Type-safe variable handling
"""
import re
from typing import Any, Dict
from app.types import MediaType


class PromptVariables:
	"""Registry of available variables for prompt templates."""
	
	TEXT_VARIABLES = {
		'text': 'Prepared text content for analysis',
		'platform': 'Platform name (VK, Telegram, etc.)',
		'source_type': 'Type of source (user, group, channel, etc.)',
		'stats': 'Content statistics dictionary',
		'total_posts': 'Total number of posts analyzed',
		'total_reactions': 'Total reactions count',
		'total_comments': 'Total comments count',
		'avg_reactions': 'Average reactions per post',
		'avg_comments': 'Average comments per post',
		'date_range_first': 'First date in content range',
		'date_range_last': 'Last date in content range',
	}
	
	IMAGE_VARIABLES = {
		'count': 'Number of images to analyze',
		'platform': 'Platform name (VK, Telegram, etc.)',
	}
	
	VIDEO_VARIABLES = {
		'count': 'Number of videos to analyze',
		'platform': 'Platform name (VK, Telegram, etc.)',
	}
	
	AUDIO_VARIABLES = {
		'count': 'Number of audio files to analyze',
		'platform': 'Platform name (VK, Telegram, etc.)',
	}
	
	UNIFIED_VARIABLES = {
		'text_analysis': 'Results from text analysis',
		'image_analysis': 'Results from image analysis',
		'video_analysis': 'Results from video analysis',
	}
	
	@classmethod
	def get_variables_for_media_type(cls, media_type: MediaType) -> dict[str, str]:
		"""
		Get available variables for a media type.
		
		Args:
			media_type: MediaType enum value
		
		Returns:
			Dictionary of variable_name -> description
		"""
		from app.utils.enum_helpers import get_enum_value
		
		media_value = get_enum_value(media_type)
		
		mapping = {
			'text': cls.TEXT_VARIABLES,
			'image': cls.IMAGE_VARIABLES,
			'video': cls.VIDEO_VARIABLES,
			'audio': cls.AUDIO_VARIABLES,
		}
		
		return mapping.get(media_value, {})
	
	@classmethod
	def get_help_text(cls, media_type: MediaType) -> str:
		"""
		Get formatted help text for available variables.
		
		Args:
			media_type: MediaType enum value
		
		Returns:
			Formatted help text string
		"""
		variables = cls.get_variables_for_media_type(media_type)
		
		if not variables:
			return "No variables available"
		
		lines = ["Доступные переменные для подстановки:"]
		for var, desc in variables.items():
			lines.append(f"  {{{var}}} - {desc}")
		
		return "\n".join(lines)


class PromptSubstitution:
	"""Utilities for variable substitution in prompt templates."""
	
	@staticmethod
	def substitute(template: str, variables: dict[str, Any]) -> str:
		"""
		Substitute variables in prompt template.
		
		Supports:
		- Simple substitution: {variable_name}
		- Nested dict access: {stats.total_posts}
		- Safe handling of missing variables
		
		Args:
			template: Prompt template with {variable} placeholders
			variables: Dictionary of variable values
		
		Returns:
			Prompt with substituted values
		
		Example:
			>>> template = "Platform: {platform}, Posts: {stats.total_posts}"
			>>> variables = {'platform': 'VK', 'stats': {'total_posts': 10}}
			>>> substitute(template, variables)
			'Platform: VK, Posts: 10'
		"""
		result = template
		
		# Find all {variable} patterns
		pattern = r'\{([^}]+)\}'
		matches = re.findall(pattern, template)
		
		for match in matches:
			placeholder = f"{{{match}}}"
			value = PromptSubstitution._get_nested_value(variables, match)
			
			if value is not None:
				# Convert to string safely
				if isinstance(value, dict):
					# For dict values, convert to formatted string
					value = str(value)
				result = result.replace(placeholder, str(value))
			# If value is None, leave placeholder as is (or could remove it)
		
		return result
	
	@staticmethod
	def _get_nested_value(data: dict[str, Any], path: str) -> Any:
		"""
		Get value from nested dictionary using dot notation.
		
		Args:
			data: Dictionary to search in
			path: Dot-separated path (e.g., 'stats.total_posts')
		
		Returns:
			Value at path or None if not found
		"""
		keys = path.split('.')
		value = data
		
		for key in keys:
			if isinstance(value, dict):
				value = value.get(key)
				if value is None:
					return None
			else:
				return None
		
		return value
	
	@staticmethod
	def prepare_text_variables(
		text: str,
		stats: dict[str, Any],
		platform_name: str,
		source_type: str
	) -> dict[str, Any]:
		"""
		Prepare standard variables for text prompt.
		
		Args:
			text: Prepared text content
			stats: Content statistics
			platform_name: Platform name
			source_type: Source type
		
		Returns:
			Dictionary of variables ready for substitution
		"""
		date_range = stats.get('date_range', {})
		
		return {
			'text': text,
			'platform': platform_name,
			'source_type': source_type,
			'stats': stats,
			'total_posts': stats.get('total_posts', 0),
			'total_reactions': stats.get('total_reactions', 0),
			'total_comments': stats.get('total_comments', 0),
			'avg_reactions': stats.get('avg_reactions_per_post', 0),
			'avg_comments': stats.get('avg_comments_per_post', 0),
			'date_range_first': date_range.get('first', ''),
			'date_range_last': date_range.get('last', ''),
		}
	
	@staticmethod
	def prepare_image_variables(count: int, platform_name: str) -> dict[str, Any]:
		"""Prepare standard variables for image prompt."""
		return {
			'count': count,
			'platform': platform_name,
		}
	
	@staticmethod
	def prepare_video_variables(count: int, platform_name: str) -> dict[str, Any]:
		"""Prepare standard variables for video prompt."""
		return {
			'count': count,
			'platform': platform_name,
		}
	
	@staticmethod
	def prepare_audio_variables(count: int, platform_name: str) -> dict[str, Any]:
		"""Prepare standard variables for audio prompt."""
		return {
			'count': count,
			'platform': platform_name,
		}
	
	@staticmethod
	def prepare_unified_variables(
		text_analysis: dict[str, Any],
		image_analysis: dict[str, Any],
		video_analysis: dict[str, Any]
	) -> dict[str, Any]:
		"""Prepare standard variables for unified summary prompt."""
		return {
			'text_analysis': text_analysis,
			'image_analysis': image_analysis,
			'video_analysis': video_analysis,
		}
