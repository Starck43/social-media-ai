"""
Preset bot scenarios for quick configuration.
Provides 8 real-world monitoring scenarios with Russian prompts.
"""

from app.types import ContentType, AnalysisType


def get_all_presets():
    """Get all available scenario presets."""
    return [
        {
            "name": "Анализ настроений аудитории",
            "icon": "😊",
            "description": "Отслеживание эмоциональной тональности постов и комментариев",
            "content_types": [ContentType.POSTS.value, ContentType.COMMENTS.value],
            "analysis_types": [AnalysisType.SENTIMENT.value, AnalysisType.KEYWORDS.value],
            "scope": {
                "sentiment_config": {
                    "detect_sarcasm": True,
                    "emotion_analysis": True,
                },
                "keywords_config": {
                    "max_keywords": 15,
                    "extract_entities": True,
                }
            },
            "ai_prompt": """Проанализируй тональность следующего контента из соцсетей.

Контент: {content}
Платформа: {platform}
Период: {date_range}

Определи:
1. Общую эмоциональную тональность (позитив/негатив/нейтрал)
2. Ключевые темы и слова
3. Упоминания брендов или продуктов
4. Наличие сарказма или иронии

Верни результат в формате JSON."""
        },

        {
            "name": "Отслеживание трендов",
            "icon": "📈",
            "description": "Выявление растущих тем и вирусного контента",
            "content_types": [ContentType.POSTS.value, ContentType.REACTIONS.value],
            "analysis_types": [
                AnalysisType.TRENDS.value,
                AnalysisType.VIRAL_DETECTION.value,
                AnalysisType.HASHTAG_ANALYSIS.value
            ],
            "scope": {
                "trends_config": {
                    "min_mentions": 10,
                    "time_window_hours": 48,
                    "track_growth": True,
                },
                "viral_detection_config": {
                    "viral_threshold": 5000,
                    "growth_rate_threshold": 3.0,
                },
                "hashtag_analysis_config": {
                    "track_trending": True,
                    "analyze_related": True,
                }
            },
            "ai_prompt": """Проанализируй тренды в следующих постах.

Данные: {total_posts} постов
Период: {date_range}
Контент: {content}

Выяви:
1. Наиболее обсуждаемые темы (минимум {trends_config.min_mentions} упоминаний)
2. Растущие хэштеги и их динамику
3. Потенциально вирусный контент
4. Новые тренды за последние {trends_config.time_window_hours} часов

Верни в JSON с рейтингом трендов."""
        },

        {
            "name": "Мониторинг негатива и токсичности",
            "icon": "🛡️",
            "description": "Автоматическое выявление негативного и токсичного контента",
            "content_types": [ContentType.COMMENTS.value, ContentType.POSTS.value],
            "analysis_types": [AnalysisType.TOXICITY.value, AnalysisType.SENTIMENT.value],
            "scope": {
                "toxicity_config": {
                    "threshold": 0.6,
                    "detect_harassment": True,
                    "detect_hate_speech": True,
                    "detect_threats": True,
                },
                "sentiment_config": {
                    "confidence_threshold": 0.75,
                }
            },
            "ai_prompt": """Проверь следующий контент на токсичность и негатив.

Контент: {content}
Источник: {platform} / {source_type}

Определи:
1. Уровень токсичности (0.0-1.0)
2. Типы негатива: оскорбления, угрозы, hate speech
3. Конкретные токсичные фразы
4. Рекомендации по модерации

Порог токсичности: {toxicity_config.threshold}

Верни детальный анализ в JSON."""
        },

        {
            "name": "Анализ вовлечённости",
            "icon": "🎯",
            "description": "Метрики engagement и эффективности контента",
            "content_types": [ContentType.POSTS.value, ContentType.REACTIONS.value, ContentType.COMMENTS.value],
            "analysis_types": [AnalysisType.ENGAGEMENT.value, AnalysisType.VIRAL_DETECTION.value],
            "scope": {
                "engagement_config": {
                    "calculate_rate": True,
                    "detect_viral": True,
                    "viral_threshold": 1000,
                },
            },
            "ai_prompt": """Проанализируй вовлечённость аудитории.

Всего постов: {total_posts}
Данные: {content}

Рассчитай:
1. Engagement rate по каждому типу контента
2. Лучшие и худшие по вовлечённости посты
3. Оптимальное время публикации
4. Типы контента с максимальной реакцией
5. Потенциально вирусный контент (порог: {engagement_config.viral_threshold})

Предоставь рекомендации в JSON."""
        },

        {
            "name": "Мониторинг конкурентов",
            "icon": "🔍",
            "description": "Отслеживание активности и стратегии конкурентов",
            "content_types": [ContentType.POSTS.value, ContentType.COMMENTS.value],
            "analysis_types": [AnalysisType.COMPETITOR_TRACKING.value, AnalysisType.TOPICS.value, AnalysisType.ENGAGEMENT.value],
            "scope": {
                "competitor_config": {
                    "track_content_strategy": True,
                    "compare_metrics": True,
                },
                "topics_config": {
                    "max_topics": 7,
                    "identify_emerging": True,
                }
            },
            "ai_prompt": """Проанализируй контент конкурента.

Источник: {source_type}
Контент: {content}
Период: {date_range}

Выяви:
1. Основные темы и стратегию контента
2. Частоту публикаций и её динамику
3. Engagement конкурента (лайки, комментарии, репосты)
4. Успешные форматы контента
5. Новые направления активности

Сравни с нашими показателями и предложи insights."""
        },

        {
            "name": "Поиск упоминаний бренда",
            "icon": "🏷️",
            "description": "Отслеживание упоминаний бренда и продуктов",
            "content_types": [ContentType.POSTS.value, ContentType.COMMENTS.value, ContentType.MENTIONS.value],
            "analysis_types": [AnalysisType.BRAND_MENTIONS.value, AnalysisType.SENTIMENT.value, AnalysisType.KEYWORDS.value],
            "scope": {
                "brand_mentions_config": {
                    "track_sentiment": True,
                    "track_reach": True,
                },
                "sentiment_config": {
                    "confidence_threshold": 0.7,
                },
                "keywords_config": {
                    "extract_entities": True,
                }
            },
            "ai_prompt": """Найди и проанализируй упоминания бренда.

Контент: {content}
Всего сообщений: {total_posts}

Определи:
1. Все упоминания бренда и продуктов
2. Тональность каждого упоминания
3. Контекст упоминания (отзыв, вопрос, жалоба, похвала)
4. Охват аудитории
5. Ключевые инфлюенсеры, упомянувшие бренд

Верни структурированный отчёт в JSON."""
        },

        {
            "name": "Анализ намерений клиентов",
            "icon": "💡",
            "description": "Выявление намерений: покупка, вопрос, жалоба, отзыв",
            "content_types": [ContentType.COMMENTS.value, ContentType.POSTS.value],
            "analysis_types": [AnalysisType.CUSTOMER_INTENT.value, AnalysisType.SENTIMENT.value, AnalysisType.KEYWORDS.value],
            "scope": {
                "intent_config": {
                    "confidence_threshold": 0.65,
                },
                "sentiment_config": {
                    "emotion_analysis": True,
                }
            },
            "ai_prompt": """Определи намерения клиентов в сообщениях.

Контент: {content}
Источник: {platform}

Для каждого сообщения определи:
1. Тип намерения: покупка, запрос информации, жалоба, отзыв, общение
2. Уровень срочности (низкий/средний/высокий)
3. Требуется ли ответ службы поддержки
4. Эмоциональное состояние клиента
5. Ключевые запросы или проблемы

Приоритизируй для обработки. Верни JSON."""
        },

        {
            "name": "Мониторинг влиятельных лиц",
            "icon": "⭐",
            "description": "Отслеживание активности инфлюенсеров и лидеров мнений",
            "content_types": [ContentType.POSTS.value, ContentType.COMMENTS.value],
            "analysis_types": [AnalysisType.INFLUENCER_ACTIVITY.value, AnalysisType.TOPICS.value, AnalysisType.ENGAGEMENT.value],
            "scope": {
                "influencer_config": {
                    "min_followers": 5000,
                    "track_engagement_rate": True,
                    "analyze_content_themes": True,
                },
                "topics_config": {
                    "max_topics": 5,
                }
            },
            "ai_prompt": """Проанализируй активность инфлюенсера.

Контент: {content}
Тип источника: {source_type}

Определи:
1. Основные темы контента инфлюенсера
2. Engagement rate постов
3. Реакцию аудитории (sentiment анализ комментариев)
4. Частоту и регулярность публикаций
5. Потенциал для коллаборации

Минимальный порог подписчиков: {influencer_config.min_followers}

Предоставь инсайты в JSON."""
        },
    ]
