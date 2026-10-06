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
		'text': 'Подготовленный текст для анализа',
		'platform': 'Название платформы (VK, Telegram и т.д.)',
		'source_type': 'Тип источника (пользователь, группа, канал и т.д.)',
		'stats': 'Словарь со статистикой контента',
		'total_posts': 'Общее количество проанализированных постов',
		'total_reactions': 'Общее количество реакций',
		'total_comments': 'Общее количество комментариев',
		'avg_reactions': 'Среднее количество реакций на пост',
		'avg_comments': 'Среднее количество комментариев на пост',
		'date_range_first': 'Первая дата в диапазоне контента',
		'date_range_last': 'Последняя дата в диапазоне контента',
		'user_activity_summary': 'Краткая сводка активности пользователя (количество постов, комментариев, лайков)',
		'liked_posts_count': 'Количество постов, которыми лайкнул пользователь',
		'comments_to_others_count': 'Количество комментариев пользователя к чужим постам',
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

	# Variables any prompt may use regardless of media, on top of the per-media
	# sets — the documented registry (docs/CHAT_BOT_SCENARIOS.md). Scope-derived
	# names (max_keywords, max_topics, sentiment_categories) resolve only once
	# the scenario's scope is known, but they are *known* names: a prompt using
	# them must not be flagged as containing unknown variables.
	COMMON_VARIABLES = {
		'date_range': 'Analysis period (2026-09-28 - 2026-10-05)',
		'source_name': 'Source name',
		'scenario_name': 'Scenario name',
		'trigger_condition': 'Reaction condition description (from AgentTask)',
		'max_keywords': 'scope.keywords.max_keywords',
		'max_topics': 'scope.topics.max_topics',
		'sentiment_categories': 'scope.sentiment.categories (comma-separated)',
		# Task-payload TARGET parameters (from AgentTask.payload)
		'brands': 'payload.brands — specific brands to track',
		'competitors': 'payload.competitors — specific competitors to analyze',
		'hashtags': 'payload.hashtags — specific hashtags to track',
		'influencer_names': 'payload.influencer_names — specific authors to analyze',
		'keywords_list': 'payload.keywords_list — specific keywords to search',
		'topic_list': 'payload.topic_list — specific topics to analyze',
		'payload_instruction': 'Rendered instruction line from all payload targets',
	}

	AVAILABLE_VARIABLES = {
		**TEXT_VARIABLES,
		**IMAGE_VARIABLES,
		**VIDEO_VARIABLES,
		**AUDIO_VARIABLES,
		**UNIFIED_VARIABLES,
		**COMMON_VARIABLES,
	}

	@classmethod
	def validate_prompt(cls, prompt: str) -> list[str]:
		"""
		Unknown variable names in a prompt, per the `AVAILABLE_VARIABLES` registry.

		Nested paths (`{stats.total_posts}`) validate against the top-level name
		only, matching how substitution resolves them. Scope-derived names are
		part of the registry, so a prompt using `{max_keywords}` passes even
		though the value only exists once the scenario's scope is known.

		Args:
			prompt: The prompt template to check.

		Returns:
			List of unknown variable names (deduplicated, in order of appearance).
		"""
		known = set(cls.AVAILABLE_VARIABLES)
		found: list[str] = []
		for match in re.findall(r"\{([^}]+)\}", prompt or ""):
			name = match.split(".")[0]
			if name not in known and name not in found:
				found.append(name)
		return found
	
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
		value: Any = data
		
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
			'date_range': _format_date_range(date_range),
			'user_activity_summary': stats.get('user_activity_summary', ''),
			'liked_posts_count': stats.get('liked_posts_count', 0),
			'comments_to_others_count': stats.get('comments_to_others_count', 0),
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


def _format_date_range(date_range: dict) -> str:
	"""A single '{first} — {last}' string from a stats date_range dict."""
	first = date_range.get('first') or ''
	last = date_range.get('last') or ''
	if not first and not last:
		return ''
	return f"{first} — {last}"
