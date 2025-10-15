"""
Prompt templates for different types of content analysis.

This module contains prompt builders for text, image, and video analysis.
"""

from typing import Dict, Any


class PromptBuilder:
	"""Builder for LLM analysis prompts."""
	
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
