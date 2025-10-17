"""
Default configurations for bot triggers.
Provides ready-to-use configs for each trigger type.
"""

# Trigger configuration defaults for auto-population
TRIGGER_CONFIG_DEFAULTS = {
    "keyword_match": {
        "keywords": ["жалоба", "проблема", "не работает"],
        "mode": "any",  # "any" = хотя бы одно слово, "all" = все слова
        "case_sensitive": False
    },
    "sentiment_threshold": {
        "threshold": 0.3,  # Порог от 0.0 до 1.0
        "direction": "below"  # "below" = ниже порога, "above" = выше порога
    },
    "activity_spike": {
        "baseline_period_hours": 24,  # Период для расчета baseline
        "spike_multiplier": 3.0  # Во сколько раз больше нормы
    },
    "user_mention": {
        "usernames": ["@brand", "@support"],
        "mode": "any"  # "any" = любое упоминание
    },
    "time_based": {},  # Пустой, используется collection_interval_hours
    "manual": {}  # Пустой, без автозапуска
}


def get_trigger_default(trigger_db_value: str) -> dict:
    """Get default configuration for specific trigger type."""
    return TRIGGER_CONFIG_DEFAULTS.get(trigger_db_value, {})
