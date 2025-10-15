"""
Hints and documentation for trigger configuration.

Provides user-friendly explanations for each trigger type and its configuration.
"""
from app.types import BotTriggerType

TRIGGER_HINTS = {
	BotTriggerType.KEYWORD_MATCH: {
		"description": "Анализировать только контент, содержащий определенные ключевые слова",
		"config_example": {
			"keywords": ["жалоба", "проблема", "не работает"],
			"mode": "any",  # "any" = хотя бы одно слово, "all" = все слова
			"case_sensitive": False  # Учитывать регистр
		},
		"use_cases": [
			"Отслеживание упоминаний негативных отзывов",
			"Мониторинг конкретных проблем",
			"Поиск ключевых тем"
		]
	},
	
	BotTriggerType.SENTIMENT_THRESHOLD: {
		"description": "Выполнять действие только при определенном уровне тональности",
		"config_example": {
			"threshold": 0.3,  # Порог от 0.0 до 1.0
			"direction": "below"  # "below" = ниже порога, "above" = выше порога
		},
		"use_cases": [
			"Автоответы только на негативные отзывы (< 0.3)",
			"Уведомления о позитивных отзывах (> 0.7)",
			"Модерация при низкой тональности"
		],
		"note": "Работает ПОСЛЕ анализа. Анализирует весь контент, действие выполняется условно."
	},
	
	BotTriggerType.ACTIVITY_SPIKE: {
		"description": "Обнаружение аномального всплеска активности",
		"config_example": {
			"baseline_period_hours": 24,  # Период для расчета baseline
			"spike_multiplier": 3.0  # Во сколько раз больше нормы
		},
		"use_cases": [
			"Обнаружение вирусного контента",
			"Алерты при кризисных ситуациях",
			"Мониторинг аномальной активности"
		],
		"note": "Сравнивает текущий объем контента с историческим средним."
	},
	
	BotTriggerType.USER_MENTION: {
		"description": "Анализировать только контент с упоминаниями определенных пользователей",
		"config_example": {
			"usernames": ["@brand", "@support", "company"],
			"mode": "any"  # "any" = любое упоминание
		},
		"use_cases": [
			"Мониторинг упоминаний бренда",
			"Отслеживание обращений к поддержке",
			"Анализ упоминаний конкурентов"
		]
	},
	
	BotTriggerType.TIME_BASED: {
		"description": "Запуск по расписанию (уже реализовано через collection_interval_hours)",
		"config_example": {},
		"use_cases": [
			"Регулярная аналитика",
			"Плановые отчеты"
		],
		"note": "Используйте поле 'Интервал сбора (часы)' вместо trigger_config."
	},
	
	BotTriggerType.MANUAL: {
		"description": "Ручной запуск без автоматических действий",
		"config_example": {},
		"use_cases": [
			"Тестирование сценариев",
			"Ручная аналитика по запросу"
		],
		"note": "Анализ выполняется, действие не выполняется автоматически."
	},
}


SCOPE_HINTS = {
	"sentiment_config": {
		"description": "Настройки анализа тональности",
		"parameters": {
			"categories": {
				"type": "list[str]",
				"description": "Категории тональности для анализа",
				"default": ["positive", "negative", "neutral"],
				"example": ["positive", "negative", "neutral", "mixed"]
			},
			"threshold": {
				"type": "float",
				"description": "Минимальная уверенность для классификации (0.0 - 1.0)",
				"default": 0.7,
				"example": 0.8
			}
		}
	},
	
	"trends_config": {
		"description": "Настройки обнаружения трендов",
		"parameters": {
			"min_mentions": {
				"type": "int",
				"description": "Минимальное количество упоминаний для тренда",
				"default": 5,
				"example": 10
			},
			"time_window_hours": {
				"type": "int",
				"description": "Временное окно для анализа (часы)",
				"default": 24,
				"example": 48
			}
		}
	},
	
	"engagement_config": {
		"description": "Настройки анализа вовлеченности",
		"parameters": {
			"metrics": {
				"type": "list[str]",
				"description": "Метрики для анализа",
				"default": ["likes", "comments", "shares"],
				"example": ["likes", "comments", "shares", "views"]
			}
		}
	},
	
	"keywords_config": {
		"description": "Настройки извлечения ключевых слов",
		"parameters": {
			"keywords": {
				"type": "list[str]",
				"description": "Список ключевых слов для отслеживания (оставьте пустым для автоизвлечения)",
				"default": [],
				"example": ["продукт", "цена", "качество"]
			},
			"max_keywords": {
				"type": "int",
				"description": "Максимальное количество извлекаемых ключевых слов",
				"default": 10,
				"example": 15
			}
		}
	},
	
	"topics_config": {
		"description": "Настройки идентификации тем",
		"parameters": {
			"max_topics": {
				"type": "int",
				"description": "Максимальное количество тем",
				"default": 5,
				"example": 10
			}
		}
	},
	
	"toxicity_config": {
		"description": "Настройки обнаружения токсичности",
		"parameters": {
			"threshold": {
				"type": "float",
				"description": "Порог токсичности для алерта (0.0 - 1.0)",
				"default": 0.7,
				"example": 0.8
			}
		}
	}
}


def get_trigger_hint(trigger_type: BotTriggerType) -> dict:
	"""Get hint for specific trigger type."""
	return TRIGGER_HINTS.get(trigger_type, {})


def get_scope_hint(analysis_type: str) -> dict:
	"""Get hint for specific analysis type scope."""
	return SCOPE_HINTS.get(f"{analysis_type}_config", {})


def format_hint_as_html(hint: dict) -> str:
	"""Format hint as HTML for admin panel."""
	html = f"<div class='hint'>"
	html += f"<p><strong>{hint.get('description', '')}</strong></p>"
	
	if "config_example" in hint:
		import json
		example = json.dumps(hint["config_example"], indent=2, ensure_ascii=False)
		html += f"<pre>{example}</pre>"
	
	if "use_cases" in hint:
		html += "<p><strong>Примеры использования:</strong></p><ul>"
		for use_case in hint["use_cases"]:
			html += f"<li>{use_case}</li>"
		html += "</ul>"
	
	if "note" in hint:
		html += f"<p class='note'>📝 {hint['note']}</p>"
	
	html += "</div>"
	return html
