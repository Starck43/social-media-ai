"""
Prompt templates for different types of content analysis.

This module contains prompt builders for text, image, and video analysis.
Supports both default templates and custom prompts with variable substitution.
"""

import re
from typing import Dict, Any, Optional, TYPE_CHECKING

from app.types import MediaType
from app.services.ai.prompt_variables import PromptSubstitution

if TYPE_CHECKING:
	from app.models import AgentScenario


class PromptBuilder:
	"""Builder for LLM analysis prompts with custom prompt support."""

	@staticmethod
	def get_prompt(
			media_type: MediaType,
			scenario: Optional['AgentScenario'] = None,
			task_payload: Optional[dict[str, Any]] = None,
			**context
	) -> str:
		"""
		Get prompt for media type, using custom or default template.

		This is the main entry point for getting prompts. It will:
		1. Check if scenario has custom prompt for this media type
		2. If yes, use custom prompt with variable substitution
		3. If no, fallback to default hardcoded prompt
		4. Auto-append JSON format instruction if not present

		Args:
			media_type: Type of media (TEXT, IMAGE, VIDEO, AUDIO)
			scenario: Optional AgentScenario with base_prompt and media_overrides
			task_payload: Task-specific parameters (brands, competitors, etc.)
			**context: Context variables for prompt (text, platform, stats, count, etc.)

		Returns:
			Complete prompt string ready for LLM

		Example:
			# With custom prompt
			prompt = PromptBuilder.get_prompt(
				MediaType.TEXT,
				scenario=my_scenario,  # has base_prompt = "Analyze {text} from {platform}"
				task_payload={"brands": ["Coca-Cola", "Sprite"]},
				text="content here",
				platform="VK"
			)

			# Without custom prompt (uses default)
			prompt = PromptBuilder.get_prompt(
				MediaType.IMAGE,
				scenario=None,
				count=5,
				platform="VK"
			)
		"""
		from app.utils.enum_helpers import get_enum_value

		# Try to get the custom prompt from the scenario: the base prompt is the
		# core instruction for every media type, and a per-media override from
		# `media_overrides` replaces it for that media only.
		custom_prompt = None
		if scenario:
			media_value = get_enum_value(media_type)
			media_overrides = scenario.media_overrides or {}
			if media_value in media_overrides:
				custom_prompt = media_overrides[media_value]
			elif scenario.base_prompt:
				custom_prompt = scenario.base_prompt

		# Use custom prompt if available
		if custom_prompt:
			# Prepare variables based on media type (includes scope, task_payload, trigger_config)
			variables = PromptBuilder._prepare_variables(
				media_type, scenario=scenario, task_payload=task_payload, **context
			)
			prompt = PromptSubstitution.substitute(custom_prompt, variables)

			# Auto-append JSON instruction if not present (with scenario for dynamic schema)
			prompt = PromptBuilder._ensure_json_instruction(prompt, media_type, scenario)
			return prompt

		# Fallback to default prompts (already have JSON instructions)
		return PromptBuilder._get_default_prompt(media_type, **context)

	@staticmethod
	def get_unified_summary_prompt(
			text_analysis: Dict[str, Any],
			image_analysis: Dict[str, Any],
			video_analysis: Dict[str, Any],
			scenario: Optional['AgentScenario'] = None
	) -> str:
		"""
		Get prompt for unified summary, using custom or default.
		
		Args:
			text_analysis: Results from text analysis
			image_analysis: Results from image analysis
			video_analysis: Results from video analysis
			scenario: Optional AgentScenario with custom summary_prompt
		
		Returns:
			Complete prompt for unified summary
		"""
		# Check for custom prompt
		if scenario and scenario.summary_prompt:
			variables = PromptSubstitution.prepare_unified_variables(
				text_analysis, image_analysis, video_analysis
			)
			prompt = PromptSubstitution.substitute(scenario.summary_prompt, variables)

			# Auto-append JSON instruction if not present
			prompt = PromptBuilder._ensure_unified_json_instruction(prompt)
			return prompt

		# Fallback to default (already has JSON instructions)
		return PromptBuilder.build_unified_summary_prompt(
			text_analysis, image_analysis, video_analysis
		)

	@staticmethod
	def _prepare_variables(
			media_type: MediaType,
			scenario: Optional['AgentScenario'] = None,
			task_payload: Optional[dict[str, Any]] = None,
			**context
	) -> dict[str, Any]:
		"""
		Prepare variables for substitution based on media type.

		This method merges:
		1. Standard variables (text, platform, stats, count, etc.)
		2. Custom variables from scenario.scope
		3. Task-specific payload (brands, competitors, hashtags)
		4. Trigger configuration from the caller's `trigger_config` context key
		5. Analysis type configs from scope (topics, sentiment, etc.)

		Priority order: system > task_payload > scope
		"""
		from app.utils.enum_helpers import get_enum_value

		media_value = get_enum_value(media_type)

		# Get standard variables based on media type
		if media_value == 'text':
			variables = PromptSubstitution.prepare_text_variables(
				text=context.get('text', ''),
				stats=context.get('stats', {}),
				platform_name=context.get('platform_name', ''),
				source_type=context.get('source_type', '')
			)
		elif media_value == 'image':
			variables = PromptSubstitution.prepare_image_variables(
				count=context.get('count', 0),
				platform_name=context.get('platform_name', '')
			)
		elif media_value == 'video':
			variables = PromptSubstitution.prepare_video_variables(
				count=context.get('count', 0),
				platform_name=context.get('platform_name', '')
			)
		elif media_value == 'audio':
			variables = PromptSubstitution.prepare_audio_variables(
				count=context.get('count', 0),
				platform_name=context.get('platform_name', '')
			)
		else:
			variables = context

		# Add custom variables from scenario.scope (if present)
		if scenario and scenario.scope:
			for key, value in scenario.scope.items():
				if key not in variables:
					variables[key] = value

			for var_name, value in PromptBuilder._flatten_scope_variables(scenario.scope).items():
				if value is not None and var_name not in variables:
					variables[var_name] = value

		# Merge task_payload variables with higher priority than scope
		# Priority: task_payload > scope for the same key
		if task_payload:
			for key, value in task_payload.items():
				if value is not None:
					variables[key] = value

			# Build instruction text from payload for target types
			payload_instruction = PromptBuilder._build_payload_instruction(task_payload, scenario)
			if payload_instruction:
				variables['payload_instruction'] = payload_instruction

		# Documented context variables
		variables.setdefault('source_name', context.get('source_name'))
		variables.setdefault('trigger_condition', context.get('trigger_condition'))
		if scenario:
			variables.setdefault('scenario_name', scenario.name)
		elif context.get('scenario_name') is not None:
			variables['scenario_name'] = context['scenario_name']

		# Add trigger_config as a separate object (if present)
		trigger_config = context.get('trigger_config')
		if trigger_config:
			variables['trigger_config'] = trigger_config

		return variables

	@staticmethod
	def _flatten_scope_variables(scope: dict) -> dict[str, str]:
		"""The documented scope-derived names, from a scenario's scope.

		`{max_keywords}` is a friendlier alias for `{keywords.max_keywords}`.
		Only the names the docs promise are flattened; everything else keeps the
		existing `{analysis_type}` object + nested-access behaviour.
		"""
		flattened: dict[str, str] = {}
		keywords = scope.get('keywords') or {}
		if isinstance(keywords, dict) and keywords.get('max_keywords') is not None:
			flattened['max_keywords'] = str(keywords['max_keywords'])
		topics = scope.get('topics') or {}
		if isinstance(topics, dict) and topics.get('max_topics') is not None:
			flattened['max_topics'] = str(topics['max_topics'])
		sentiment = scope.get('sentiment') or {}
		categories = sentiment.get('categories') if isinstance(sentiment, dict) else None
		if isinstance(categories, (list, tuple)) and categories:
			flattened['sentiment_categories'] = ', '.join(str(c) for c in categories)
		return flattened

	@staticmethod
	def _build_payload_instruction(
			task_payload: dict[str, Any],
			scenario: Optional['AgentScenario'] = None
	) -> str:
		"""Build instruction text from payload target values.

		Extracts specific targets (brands, competitors, hashtags, etc.)
		from task_payload and formats them as instruction text for the prompt.
		"""
		from app.services.ai.param_registry import PAYLOAD_PARAMS

		instructions = []
		analysis_types = scenario.analysis_types if scenario else []

		if 'brands' in task_payload and task_payload['brands']:
			brands = task_payload['brands']
			brand_list = ", ".join(f'"{b}"' for b in brands) if isinstance(brands, list) else str(brands)
			instructions.append(f"Отслеживай бренды: {brands}")

		if 'competitors' in task_payload and task_payload['competitors']:
			competitors = task_payload['competitors']
			comp_list = ", ".join(f'"{c}"' for c in competitors) if isinstance(competitors, list) else str(competitors)
			instructions.append(f"Анализируй конкурентов: {comp_list}")

		if 'hashtags' in task_payload and task_payload['hashtags']:
			hashtags = task_payload['hashtags']
			ht_list = ", ".join(f'#{h}' if not h.startswith('#') else h for h in hashtags) if isinstance(hashtags, list) else str(hashtags)
			instructions.append(f"Отслеживай хэштеги: {ht_list}")

		if 'influencer_names' in task_payload and task_payload['influencer_names']:
			influencers = task_payload['influencer_names']
			inf_list = ", ".join(f'@{i}' if not i.startswith('@') else i for i in influencers) if isinstance(influencers, list) else str(influencers)
			instructions.append(f"Анализируй авторов: {inf_list}")

		if 'keywords_list' in task_payload and task_payload['keywords_list']:
			keywords = task_payload['keywords_list']
			kw_list = ", ".join(f'"{k}"' for k in keywords) if isinstance(keywords, list) else str(keywords)
			instructions.append(f"Ищи ключевые слова: {kw_list}")

		if 'topic_list' in task_payload and task_payload['topic_list']:
			topics = task_payload['topic_list']
			top_list = ", ".join(f'"{t}"' for t in topics) if isinstance(topics, list) else str(topics)
			instructions.append(f"Анализируй темы: {top_list}")

		if instructions:
			return "УКАЗАНИЯ К АНАЛИЗУ: " + "; ".join(instructions) + "."
		return ""

	@staticmethod
	def _ensure_json_instruction(
		prompt: str,
		media_type: MediaType,
		scenario: Optional['AgentScenario'] = None
	) -> str:
		"""
		Ensure prompt has JSON format instruction appended if not present.
		
		Args:
			prompt: Original prompt text
			media_type: Type of media being analyzed
			scenario: AgentScenario with analysis_types and scope
		
		Returns:
			Prompt with JSON instruction appended if needed
		"""
		from app.utils.enum_helpers import get_enum_value
		from app.services.ai.json_schema_builder import JSONSchemaBuilder

		# JSON detection: a user-written JSON structure (an object whose keys are
		# quoted strings) means the prompt already says what fields to return, so
		# the auto-generated schema is skipped. `{text}` placeholders are not JSON
		# (no quotes), so they never trigger this.
		if re.search(r'\{[^{}]*"[^"]+"\s*:', prompt or ''):
			# Already has a JSON instruction
			return prompt

		# If scenario provided, use dynamic schema builder
		if scenario and scenario.analysis_types:
			json_instruction = JSONSchemaBuilder.build_json_instruction(
				analysis_types=scenario.analysis_types,
				scope=scenario.scope
			)
			return prompt + json_instruction

		# Fallback to media-type-specific schemas for backward compatibility
		media_value = get_enum_value(media_type)

		if media_value == 'text':
			json_instruction = """

ВАЖНО: Верни результат СТРОГО в JSON формате:
{
	"analysis_title": "краткий заголовок анализа (3-7 слов), отражающий главную тему",
	"analysis_summary": "развернутое описание ситуации (2-4 предложения): что происходит, кого/что ищут, какие требования выделяют, какие паттерны видны",
	"main_topics": ["список из 3-5 главных тем контента"],
	"overall_mood": "общее настроение одной фразой (например: 'позитивное и воодушевляющее')",
	"highlights": ["список из 2-3 самых выделяющихся моментов"],
	"sentiment_score": "число от 0.0 (негатив) до 1.0 (позитив)"
}

Не добавляй текст до или после JSON.
"""
		elif media_value == 'image':
			json_instruction = """

ВАЖНО: Верни результат СТРОГО в JSON формате:
{
	"analysis_title": "краткий заголовок анализа изображений (3-7 слов)",
	"analysis_summary": "описание что изображено и какое впечатление производит (2-3 предложения)",
	"visual_themes": ["список из 2-4 визуальных тем (например: 'природные пейзажи')"],
	"dominant_colors": ["список из 2-3 доминирующих цветов на русском"],
	"mood": "эмоциональное впечатление одной фразой (например: 'спокойное и умиротворённое')"
}

Не добавляй текст до или после JSON.
"""
		elif media_value == 'video':
			json_instruction = """

ВАЖНО: Верни результат СТРОГО в JSON формате:
{
	"analysis_title": "краткий заголовок анализа видео (3-7 слов)",
	"analysis_summary": "описание о чем видео и что в них показывается (2-3 предложения)",
	"video_types": ["типы видео: 'короткие ролики'/'истории'/'образовательные'/'развлекательные'"],
	"main_themes": ["список из 2-4 тем видео"],
	"content_style": "эмоциональное впечатление одной фразой (например: 'весёлое и развлекательное')"
}

Не добавляй текст до или после JSON.
"""
		elif media_value == 'audio':
			json_instruction = """

ВАЖНО: Верни результат СТРОГО в JSON формате:
{
	"analysis_title": "краткий заголовок анализа аудио (3-7 слов)",
	"audio_types": ["типы аудио: 'музыка'/'подкаст'/'голосовое сообщение'/'интервью'"],
	"content_themes": ["список из 2-4 тем аудио"],
	"mood": "эмоциональное впечатление одной фразой (например: 'энергичное и бодрое')"
}

Не добавляй текст до или после JSON.
"""
		else:
			# Generic JSON instruction
			json_instruction = """

ВАЖНО: Верни результат в JSON формате.
"""

		return prompt + json_instruction

	@staticmethod
	def _ensure_unified_json_instruction(prompt: str) -> str:
		"""
		Ensure unified summary prompt has JSON format instruction.

		Args:
			prompt: Original unified summary prompt

		Returns:
			Prompt with JSON instruction appended if needed
		"""
		# JSON detection: a user-written JSON structure means the prompt already
		# says what to return, so the auto schema is skipped.
		if re.search(r'\{[^{}]*"[^"]+"\s*:', prompt or ''):
			return prompt

		# Append unified JSON instruction
		json_instruction = """

ВАЖНО: Верни результат СТРОГО в JSON формате:
{
	"analysis_title": "краткий заголовок общего анализа (3-7 слов)",
	"overall_sentiment": "общая тональность: 'позитивная'/'негативная'/'нейтральная'/'смешанная'",
	"main_themes": ["список из 3-5 главных тем всего контента"],
	"key_insights": ["список из 2-3 ключевых инсайтов или выводов"],
	"content_summary": "краткое описание всего контента одним абзацем (3-5 предложений)"
}

Не добавляй текст до или после JSON.
"""

		return prompt + json_instruction

	@staticmethod
	def _get_default_prompt(media_type: MediaType, **context) -> str:
		"""Get default hardcoded prompt for media type."""
		from app.utils.enum_helpers import get_enum_value

		media_value = get_enum_value(media_type)

		if media_value == 'text':
			return PromptBuilder.build_text_prompt(
				text=context.get('text', ''),
				stats=context.get('stats', {}),
				platform_name=context.get('platform_name', ''),
				source_type=context.get('source_type', '')
			)
		elif media_value == 'image':
			return PromptBuilder.build_image_prompt(
				count=context.get('count', 0),
				platform_name=context.get('platform_name', '')
			)
		elif media_value == 'video':
			return PromptBuilder.build_video_prompt(
				count=context.get('count', 0),
				platform_name=context.get('platform_name', '')
			)
		elif media_value == 'audio':
			return PromptBuilder.build_audio_prompt(
				count=context.get('count', 0),
				platform_name=context.get('platform_name', '')
			)
		else:
			raise ValueError(f"Unknown media type: {media_type}")

	@staticmethod
	def build_text_prompt(text: str, stats: dict[str, Any], platform_name: str, source_type: str) -> str:
		"""
		Build comprehensive text analysis prompt.
		
		Args:
			text: Prepared text content
			stats: Content statistics
			platform_name: Social media platform name
			source_type: Type of source
			
		Returns:
			Complete prompt for text analysis
		"""
		return f"""
Проанализируй контент из социальной сети и предоставь комплексный анализ в JSON формате.

ИСХОДНЫЕ ДАННЫЕ:
— Тип источника: {source_type}
— Платформа: {platform_name}
— Общее количество постов: {stats.get('total_posts', 0)}
— Период: {stats.get('date_range', {}).get('first')} — {stats.get('date_range', {}).get('last')}

КОНТЕНТ ДЛЯ АНАЛИЗА:
{text}

ВЕРНИ ОТВЕТ В СЛЕДУЮЩЕЙ JSON СТРУКТУРЕ:
{{
	"sentiment_analysis": {{
		"overall_sentiment": "positive/negative/neutral/mixed",
		"sentiment_score": 0.0-1.0,
		"dominant_emotions": ["эмоция1", "эмоция2", "эмоция3"],
		"positive_topics": ["тема1", "тема2"],
		"negative_topics": ["тема1", "тема2"],
		"sentiment_summary": "краткое описание эмоционального настроя"
	}},
	"topic_analysis": {{
		"main_topics": ["тема1", "тема2", "тема3", "тема4", "тема5"],
		"topic_prevalence": {{"тема1": 0.25, "тема2": 0.20, "тема3": 0.15}},
		"emerging_topics": ["новая тема1", "новая тема2"],
		"topic_summary": "краткое описание тематического содержания"
	}},
	"engagement_analysis": {{
		"engagement_level": "high/medium/low",
		"engagement_score": 0.0-1.0,
		"viral_potential": "high/medium/low",
		"audience_interest": ["интерес1", "интерес2"],
		"engagement_summary": "краткое описание вовлеченности"
	}},
	"content_analysis": {{
		"content_quality": "high/medium/low",
		"content_types": ["тип1", "тип2"],
		"key_phrases": ["фраза1", "фраза2", "фраза3"],
		"hashtags": ["#хэштег1", "#хэштег2"],
		"content_summary": "краткое описание качества контента"
	}},
	"audience_insights": {{
		"audience_mood": "описание настроения аудитории",
		"key_concerns": ["проблема1", "проблема2"],
		"suggestions": ["предложение1", "предложение2"],
		"audience_summary": "краткое описание аудитории"
	}}
}}

Будь точным и объективным в анализе. Используй статистические данные для подкрепления выводов.
"""

	@staticmethod
	def build_image_prompt(count: int, platform_name: str) -> str:
		"""
		Build image analysis prompt.
		
		Args:
			count: Number of images to analyze
			platform_name: Social media platform name
			
		Returns:
			Prompt for image analysis
		"""
		return f"""
Проанализируй {count} изображений из социальной сети {platform_name}.

ЗАДАЧИ АНАЛИЗА:
1. Определи основные объекты и сцены на изображениях
2. Выяви преобладающую тематику визуального контента
3. Оцени эмоциональную окраску изображений
4. Определи контекст использования (реклама, личные фото, новости и т.д.)
5. Выяви повторяющиеся визуальные паттерны или стили

ВЕРНИ ОТВЕТ В JSON ФОРМАТЕ:
{{
	"visual_themes": ["тема1", "тема2", "тема3"],
	"detected_objects": {{
		"людей": количество_изображений,
		"природа": количество_изображений,
		"товары": количество_изображений
	}},
	"emotional_tone": "positive/negative/neutral",
	"content_context": "advertising/personal/news/art/other",
	"visual_style": ["стиль1", "стиль2"],
	"brand_elements": ["элемент1", "элемент2"],
	"text_in_images": {{
		"has_text": true/false,
		"detected_text": ["текст1", "текст2"]
	}},
	"image_summary": "краткое описание визуального контента"
}}

Будь точным в определении объектов и контекста изображений.
"""

	@staticmethod
	def build_video_prompt(count: int, platform_name: str) -> str:
		"""
		Build video analysis prompt.
		
		Args:
			count: Number of videos to analyze
			platform_name: Social media platform name
			
		Returns:
			Prompt for video analysis
		"""
		return f"""
Проанализируй {count} видео из социальной сети {platform_name}.

ЗАДАЧИ АНАЛИЗА:
1. Определи тип и формат видео (короткие ролики, длинные видео, истории)
2. Выяви основную тематику и содержание
3. Оцени динамику и темп контента
4. Определи целевую аудиторию и назначение видео
5. Выяви визуальные и аудио-элементы (музыка, речь, эффекты)

ВЕРНИ ОТВЕТ В JSON ФОРМАТЕ:
{{
	"video_types": ["short_form/long_form/stories/reels"],
	"content_themes": ["тема1", "тема2", "тема3"],
	"content_purpose": "entertainment/educational/promotional/news/personal",
	"target_audience": "молодежь/взрослые/профессионалы/широкая аудитория",
	"visual_elements": {{
		"has_text_overlays": true/false,
		"has_effects": true/false,
		"filming_style": "professional/amateur/mixed"
	}},
	"audio_elements": {{
		"has_music": true/false,
		"has_speech": true/false,
		"has_sound_effects": true/false
	}},
	"engagement_factors": ["фактор1", "фактор2"],
	"video_summary": "краткое описание видео контента"
}}

Будь точным в классификации типов видео и определении их назначения.
"""

	@staticmethod
	def build_audio_prompt(count: int, platform_name: str) -> str:
		"""
		Build audio analysis prompt.
		
		Args:
			count: Number of audio files to analyze
			platform_name: Social media platform name
			
		Returns:
			Prompt for audio analysis
		"""
		return f"""
Проанализируй {count} аудио файлов из социальной сети {platform_name}.

ЗАДАЧИ АНАЛИЗА:
1. Определи тип аудио контента (музыка, подкаст, голосовое сообщение, звуковые эффекты)
2. Выяви основную тематику и содержание
3. Оцени качество и характер аудио (профессиональная запись, любительская)
4. Определи настроение и эмоциональную окраску
5. Выяви целевую аудиторию и назначение

ВЕРНИ ОТВЕТ В JSON ФОРМАТЕ:
{{
	"audio_types": ["music/podcast/voice_message/sound_effects/other"],
	"content_themes": ["тема1", "тема2", "тема3"],
	"audio_quality": "professional/amateur/mixed",
	"emotional_tone": "positive/negative/neutral/mixed",
	"content_purpose": "entertainment/educational/communication/promotional",
	"language_detected": ["язык1", "язык2"],
	"has_speech": true/false,
	"has_music": true/false,
	"target_audience": "молодежь/взрослые/профессионалы/широкая аудитория",
	"engagement_factors": ["фактор1", "фактор2"],
	"audio_summary": "краткое описание аудио контента"
}}

Будь точным в классификации типов аудио и определении их назначения.
"""

	@staticmethod
	def build_unified_summary_prompt(
			text_analysis: dict[str, Any],
			image_analysis: dict[str, Any],
			video_analysis: dict[str, Any]
	) -> str:
		"""
		Build prompt for creating unified summary from multiple analyses.
		
		Args:
			text_analysis: Results from text analysis
			image_analysis: Results from image analysis
			video_analysis: Results from video analysis
			
		Returns:
			Prompt for creating unified summary
		"""
		return f"""
У тебя есть результаты анализа контента из разных источников. Создай единое общее резюме (summary).

ТЕКСТОВЫЙ АНАЛИЗ:
{text_analysis}

АНАЛИЗ ИЗОБРАЖЕНИЙ:
{image_analysis}

АНАЛИЗ ВИДЕО:
{video_analysis}

СОЗДАЙ ЕДИНОЕ РЕЗЮМЕ В JSON ФОРМАТЕ:
{{
	"overall_sentiment": "positive/negative/neutral/mixed",
	"main_themes": ["тема1", "тема2", "тема3"],
	"content_distribution": {{
		"text_weight": 0.0-1.0,
		"visual_weight": 0.0-1.0,
		"video_weight": 0.0-1.0
	}},
	"key_insights": [
		"ключевой инсайт 1",
		"ключевой инсайт 2",
		"ключевой инсайт 3"
	],
	"audience_engagement": {{
		"text_engagement": "high/medium/low",
		"visual_engagement": "high/medium/low",
		"video_engagement": "high/medium/low",
		"overall_engagement": "high/medium/low"
	}},
	"content_strategy_recommendations": [
		"рекомендация 1",
		"рекомендация 2"
	],
	"unified_summary": "Общее резюме по всему проанализированному контенту (2-3 предложения)"
}}

Объедини все данные в целостную картину и дай практические рекомендации.
"""
