"""
Preset configurations for bot scenarios.
Used in admin UI to provide quick setup via radiobuttons.

Presets include:
- analysis_types: which types of analysis to run
- content_types: which media to monitor
- trigger_type: when to trigger
- scope: analysis parameters
"""

from typing import Any
from app.types import AnalysisType, ContentType, BotTriggerType

# ============================================================
# RESPONSE FORMAT PRESETS
# ============================================================

EVENT_BASED_FORMAT = {
	"mode": "themes",
	"group_by": "date",
	"include_metadata": True,
	"format": "structured"
}

TOPIC_BASED_FORMAT = {
	"mode": "topics",
	"group_by": "theme",
	"detect_emerging": True,
	"min_topic_weight": 0.1,
	"max_topics": 10,
	"link_related": True,
	"format": "structured"
}

RESPONSE_FORMAT_PRESETS = {
	"event_based": {
		"name": "📅 Анализ по событиям/датам",
		"description": "Группировка по дням, каждая дата = отдельная запись",
		"config": EVENT_BASED_FORMAT,
		"use_cases": "Мониторинг активности, анализ динамики, отслеживание изменений"
	},
	"topic_based": {
		"name": "🎯 Анализ по темам",
		"description": "ИИ автоматически определяет темы и группирует контент",
		"config": TOPIC_BASED_FORMAT,
		"use_cases": "Поиск трендов, анализ обсуждений, кластеризация контента"
	}
}

# ============================================================
# OUTPUT DISPLAY PRESETS
# ============================================================

MINIMAL_DISPLAY = {
	"show_sentiment_emoji": False,
	"show_keywords": False,
	"show_source_links": False,
	"show_trigger_reason": False,
	"show_content_annotations": False,
	"compact_mode": True
}

STANDARD_DISPLAY = {
	"show_sentiment_emoji": True,
	"show_keywords": True,
	"show_source_links": True,
	"show_trigger_reason": False,
	"show_content_annotations": False,
	"compact_mode": False
}

DETAILED_DISPLAY = {
	"show_sentiment_emoji": True,
	"show_keywords": True,
	"show_source_links": True,
	"show_trigger_reason": True,
	"show_content_annotations": True,
	"show_stats": True,
	"show_metadata": True,
	"compact_mode": False
}

OUTPUT_DISPLAY_PRESETS = {
	"minimal": {
		"name": "📋 Минимальный",
		"description": "Только основная информация, компактный вид",
		"config": MINIMAL_DISPLAY
	},
	"standard": {
		"name": "📊 Стандартный",
		"description": "Сбалансированное отображение с ключевыми элементами",
		"config": STANDARD_DISPLAY
	},
	"detailed": {
		"name": "📈 Детальный",
		"description": "Полная информация: эмодзи, ключевые слова, ссылки, триггеры, метаданные",
		"config": DETAILED_DISPLAY
	}
}

# ============================================================
# SCOPE PRESETS (event_based flag + analysis_types params)
# ============================================================

EVENT_MONITORING_SCOPE = {
	"event_based": True,
	"max_events_per_analysis": 50,
	"sentiment": {
		"categories": ["Позитивный", "Нейтральный", "Негативный"],
		"detect_sarcasm": True
	},
	"keywords": {
		"entity_types": ["Персоны", "Организации", "Продукты"],
		"max_keywords": 10
	}
}

TOPIC_DETECTION_SCOPE = {
	"event_based": False,
	"min_topic_mentions": 5,
	"topics": {
		"categories": ["Политика", "Технологии", "Экономика", "Общество", "Культура"],
		"max_topics": 5,
		"identify_emerging": True
	},
	"trends": {
		"trend_types": ["Восходящий тренд", "Вирусный", "Стабильный"],
		"min_mentions": 10
	}
}

ENGAGEMENT_TRACKING_SCOPE = {
	"event_based": True,
	"max_events_per_analysis": 100,
	"engagement": {
		"levels": ["Высокий", "Средний", "Низкий"],
		"detect_viral": True,
		"viral_threshold": 1000
	},
	"sentiment": {
		"categories": ["Позитивный", "Нейтральный", "Негативный"]
	}
}

SCOPE_PRESETS = {
	"event_monitoring": {
		"name": "📅 Мониторинг событий",
		"description": "Анализ по дням с sentiment и keywords",
		"config": EVENT_MONITORING_SCOPE,
		"recommended_format": "event_based"
	},
	"topic_detection": {
		"name": "🎯 Поиск тем",
		"description": "ИИ находит темы, тренды и группирует контент",
		"config": TOPIC_DETECTION_SCOPE,
		"recommended_format": "topic_based"
	},
	"engagement_tracking": {
		"name": "📊 Отслеживание вовлечённости",
		"description": "Анализ вирусности и engagement по событиям",
		"config": ENGAGEMENT_TRACKING_SCOPE,
		"recommended_format": "event_based"
	}
}


def get_scope_preset(preset_key: str) -> dict[str, Any]:
	"""Get scope configuration by preset key."""
	preset = SCOPE_PRESETS.get(preset_key)
	return preset["config"] if preset else EVENT_MONITORING_SCOPE


# Complete scenario presets with all settings
SCENARIO_PRESETS = {
	"basic_monitoring": {
		"id": "basic_monitoring",
		"name": "📊 Базовый мониторинг",
		"description": "Простой мониторинг с sentiment и keywords по расписанию",
		"analysis_types": ["sentiment", "keywords"],
		"content_types": ["text"],
		"trigger_type": "TIME_BASED",  # Enum NAME, not db_value
		"trigger_config": {},
		"scope": EVENT_MONITORING_SCOPE,
	},
	"negative_tracking": {
		"id": "negative_tracking",
		"name": "⚠️ Отслеживание негатива",
		"description": "Мониторинг негативных отзывов и токсичности",
		"analysis_types": ["sentiment", "toxicity", "keywords"],
		"content_types": ["text"],
		"trigger_type": "SENTIMENT_THRESHOLD",  # Enum NAME
		"trigger_config": {
			"threshold": 0.3,
			"direction": "below"
		},
		"scope": {
			"event_based": True,
			"sentiment": {
				"categories": ["Позитивный", "Нейтральный", "Негативный"],
				"detect_sarcasm": True
			},
			"toxicity": {
				"threshold": 0.7,
				"detect_hate_speech": True
			},
			"keywords": {
				"max_keywords": 15
			}
		},
	},
	"viral_detection": {
		"id": "viral_detection",
		"name": "🔥 Детектор вирусного контента",
		"description": "Отслеживание вирусного потенциала и всплесков активности",
		"analysis_types": ["engagement", "viral_detection", "trends"],
		"content_types": ["text", "image", "video"],
		"trigger_type": "ACTIVITY_SPIKE",  # Enum NAME
		"trigger_config": {
			"baseline_period_hours": 24,
			"spike_multiplier": 3.0
		},
		"scope": ENGAGEMENT_TRACKING_SCOPE,
	},
	"topic_research": {
		"id": "topic_research",
		"name": "🎯 Исследование тем",
		"description": "Глубокий анализ тем, трендов и обсуждений",
		"analysis_types": ["topics", "trends", "keywords", "sentiment"],
		"content_types": ["text"],
		"trigger_type": "TIME_BASED",  # Enum NAME
		"trigger_config": {},
		"scope": TOPIC_DETECTION_SCOPE,
	},
	"brand_mentions": {
		"id": "brand_mentions",
		"name": "🏷️ Мониторинг бренда",
		"description": "Отслеживание упоминаний бренда и реакций",
		"analysis_types": ["brand_mentions", "sentiment", "keywords"],
		"content_types": ["text"],
		"trigger_type": "KEYWORD_MATCH",  # Enum NAME
		"trigger_config": {
			"keywords": ["бренд", "компания", "@brand"],
			"mode": "any",
			"case_sensitive": False
		},
		"scope": {
			"event_based": True,
			"brand_mentions": {
				"brand_names": [],
				"track_sentiment": True,
				"track_reach": True
			},
			"sentiment": {
				"categories": ["Позитивный", "Нейтральный", "Негативный"]
			},
			"keywords": {
				"max_keywords": 10
			}
		},
	}
}


def get_all_presets() -> list[dict[str, Any]]:
	"""
	Get all presets for admin UI.
	Returns list of dicts with preset information for radiobutton display.
	"""
	return list(SCENARIO_PRESETS.values())


def get_preset(preset_id: str) -> dict[str, Any] | None:
	"""Get specific preset by ID."""
	return SCENARIO_PRESETS.get(preset_id)
