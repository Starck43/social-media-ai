#!/usr/bin/env python3
"""
Скрипт для заполнения БД тестовыми LLM провайдерами и сценариями.
Использование: python scripts/seed_data.py
"""
import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.models import LLMProvider, BotScenario


async def seed_llm_providers():
    """Создание LLM провайдеров."""
    print("📦 Создание LLM провайдеров...")
    
    providers = [
        {
            "name": "DeepSeek Chat",
            "description": "DeepSeek LLM для текстового анализа. Быстрый и доступный по цене.",
            "provider_type": "deepseek",
            "api_url": "https://api.deepseek.com/v1/chat/completions",
            "api_key_env": "DEEPSEEK_API_KEY",
            "model_name": "deepseek-chat",
            "capabilities": ["text"],
            "config": {"temperature": 0.2, "max_tokens": 2000},
            "is_active": True
        },
        {
            "name": "OpenAI GPT-3.5 Turbo",
            "description": "OpenAI GPT-3.5 Turbo для быстрого анализа текста. Доступная цена.",
            "provider_type": "openai",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "api_key_env": "OPENAI_API_KEY",
            "model_name": "gpt-3.5-turbo",
            "capabilities": ["text"],
            "config": {"temperature": 0.3, "max_tokens": 2000},
            "is_active": False
        },
        {
            "name": "OpenAI GPT-4 Turbo",
            "description": "OpenAI GPT-4 Turbo для комплексного анализа текста. Высокое качество.",
            "provider_type": "openai",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "api_key_env": "OPENAI_API_KEY",
            "model_name": "gpt-4-turbo-preview",
            "capabilities": ["text"],
            "config": {"temperature": 0.2, "max_tokens": 3000},
            "is_active": False
        },
        {
            "name": "OpenAI GPT-4 Vision",
            "description": "OpenAI GPT-4 с поддержкой анализа изображений и видео.",
            "provider_type": "openai",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "api_key_env": "OPENAI_API_KEY",
            "model_name": "gpt-4-vision-preview",
            "capabilities": ["text", "image", "video"],
            "config": {"temperature": 0.1, "max_tokens": 3000},
            "is_active": False
        },
        {
            "name": "Anthropic Claude 3",
            "description": "Anthropic Claude 3 для анализа текста. Хорошо понимает контекст.",
            "provider_type": "anthropic",
            "api_url": "https://api.anthropic.com/v1/messages",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model_name": "claude-3-opus-20240229",
            "capabilities": ["text"],
            "config": {"temperature": 0.2, "max_tokens": 2000},
            "is_active": False
        }
    ]
    
    created_providers = {}
    for provider_data in providers:
        try:
            # Проверяем существование
            existing = await LLMProvider.objects.filter(name=provider_data["name"])
            if existing:
                provider = existing[0]
                print(f"  ⚠️  Провайдер '{provider.name}' уже существует")
            else:
                provider = await LLMProvider.objects.create(**provider_data)
                print(f"  ✅ Создан провайдер: {provider.name}")
            
            created_providers[provider.name] = provider
        except Exception as e:
            print(f"  ❌ Ошибка создания провайдера {provider_data['name']}: {e}")
    
    return created_providers


async def seed_bot_scenarios(providers):
    """Создание сценариев бота."""
    print("\n🤖 Создание сценариев бота...")
    
    # Получаем провайдеров
    deepseek = providers.get("DeepSeek Chat")
    gpt4v = providers.get("OpenAI GPT-4 Vision")
    
    if not deepseek:
        print("  ⚠️  DeepSeek провайдер не найден, используем первый доступный")
        all_providers = await LLMProvider.objects.all()
        deepseek = all_providers[0] if all_providers else None
    
    if not deepseek:
        print("  ❌ Нет доступных LLM провайдеров!")
        return
    
    scenarios = [
        {
            "name": "Анализ настроений клиентов",
            "description": "Отслеживание эмоционального настроя аудитории, выявление позитивных и негативных тенденций",
            "content_types": ["posts", "comments"],
            "analysis_types": ["sentiment", "topics"],
            "scope": {
                "sentiment_config": {
                    "categories": ["positive", "negative", "neutral"],
                    "track_emotions": True
                },
                "topics_config": {
                    "max_topics": 5
                }
            },
            "ai_prompt": """Проанализируй настроение аудитории в следующем контенте из {platform}:

КОНТЕНТ:
{content}

Определи:
1. Общий эмоциональный настрой (позитивный/негативный/нейтральный)
2. Преобладающие эмоции (радость, разочарование, гнев, интерес и т.д.)
3. Основные темы, вызывающие позитивную реакцию
4. Основные темы, вызывающие негативную реакцию
5. Ключевые фразы и слова, которые используют пользователи

Верни результат в JSON формате с полями: overall_sentiment, dominant_emotions, positive_topics, negative_topics, key_phrases""",
            "action_type": "NOTIFICATION",
            "is_active": True,
            "cooldown_minutes": 60,
            "text_llm_provider_id": deepseek.id,
            "image_llm_provider_id": None,
            "video_llm_provider_id": None
        },
        {
            "name": "Мониторинг упоминаний бренда",
            "description": "Отслеживание упоминаний бренда/продукта, анализ контекста и тональности",
            "content_types": ["posts", "comments", "mentions"],
            "analysis_types": ["sentiment", "keywords", "brand_mentions"],
            "scope": {
                "keywords_config": {
                    "track_keywords": True,
                    "min_mentions": 2
                }
            },
            "ai_prompt": """Проанализируй упоминания бренда/продукта в контенте из {platform}:

КОНТЕНТ:
{content}

Для каждого упоминания определи:
1. Контекст упоминания (положительный/отрицательный/нейтральный/вопрос)
2. Связанные темы и проблемы
3. Есть ли сравнение с конкурентами
4. Типичные вопросы и проблемы пользователей
5. Возможности для улучшения репутации

Верни результат в JSON формате.""",
            "action_type": "NOTIFICATION",
            "is_active": True,
            "cooldown_minutes": 30,
            "text_llm_provider_id": deepseek.id,
            "image_llm_provider_id": None,
            "video_llm_provider_id": None
        },
        {
            "name": "Отслеживание трендов",
            "description": "Выявление новых тем, вирусного контента и растущих интересов аудитории",
            "content_types": ["posts", "reactions"],
            "analysis_types": ["trends", "viral_detection", "topics"],
            "scope": {
                "trends_config": {
                    "min_mentions": 3,
                    "time_window": "24h"
                }
            },
            "ai_prompt": """Проанализируй контент из {platform} и найди тренды:

КОНТЕНТ ({total_posts} постов):
{content}

Определи:
1. Новые темы, которые набирают популярность
2. Вирусный контент (посты с высокой вовлеченностью)
3. Изменения в интересах аудитории
4. Хештеги и фразы, которые активно используются
5. Прогноз: какие темы будут актуальны

Верни результат в JSON формате.""",
            "action_type": "NOTIFICATION",
            "is_active": True,
            "cooldown_minutes": 120,
            "text_llm_provider_id": deepseek.id,
            "image_llm_provider_id": None,
            "video_llm_provider_id": None
        },
        {
            "name": "Экспресс-анализ",
            "description": "Быстрый обзор: основные темы, настроение, активность",
            "content_types": ["posts"],
            "analysis_types": ["sentiment", "topics"],
            "scope": {
                "topics_config": {
                    "max_topics": 3
                }
            },
            "ai_prompt": """Сделай быстрый анализ контента из {platform}:

КОНТЕНТ ({total_posts} постов):
{content}

Кратко (2-3 предложения):
1. О чём говорят (главные темы)
2. Как говорят (общее настроение)
3. Что выделяется (необычное или важное)

Верни результат в JSON формате с полями: main_topics, overall_mood, highlights""",
            "action_type": None,
            "is_active": True,
            "cooldown_minutes": 15,
            "text_llm_provider_id": deepseek.id,
            "image_llm_provider_id": None,
            "video_llm_provider_id": None
        }
    ]
    
    # Если есть GPT-4 Vision, добавляем сценарий с мультимедиа
    if gpt4v:
        scenarios.append({
            "name": "Полный комплексный анализ",
            "description": "Глубокий анализ всех типов контента (текст, фото, видео)",
            "content_types": ["posts", "comments", "videos", "reactions"],
            "analysis_types": ["sentiment", "topics", "engagement", "trends"],
            "scope": {
                "sentiment_config": {"track_emotions": True},
                "topics_config": {"max_topics": 10},
                "engagement_config": {"metrics": ["likes", "comments", "shares"]}
            },
            "ai_prompt": """Выполни комплексный анализ всего контента из {platform}.

Проанализируй тональность, темы, вовлеченность, визуальный контент.
Дай рекомендации по улучшению.

Верни подробный анализ в JSON формате.""",
            "action_type": "NOTIFICATION",
            "is_active": True,
            "cooldown_minutes": 240,
            "text_llm_provider_id": deepseek.id,
            "image_llm_provider_id": gpt4v.id,
            "video_llm_provider_id": gpt4v.id
        })
    
    for scenario_data in scenarios:
        try:
            # Проверяем существование
            existing = await BotScenario.objects.filter(name=scenario_data["name"])
            if existing:
                print(f"  ⚠️  Сценарий '{scenario_data['name']}' уже существует")
            else:
                scenario = await BotScenario.objects.create(**scenario_data)
                print(f"  ✅ Создан сценарий: {scenario.name}")
        except Exception as e:
            print(f"  ❌ Ошибка создания сценария {scenario_data['name']}: {e}")


async def main():
    """Главная функция."""
    print("="*70)
    print("🌱 ЗАПОЛНЕНИЕ БАЗЫ ДАННЫХ ТЕСТОВЫМИ ДАННЫМИ")
    print("="*70)
    print()
    
    try:
        # Создаем провайдеров
        providers = await seed_llm_providers()
        
        # Создаем сценарии
        await seed_bot_scenarios(providers)
        
        print("\n" + "="*70)
        print("✅ ДАННЫЕ УСПЕШНО ДОБАВЛЕНЫ!")
        print("="*70)
        print()
        print("Следующие шаги:")
        print("  1. Добавьте API ключи в .env файл:")
        print("     DEEPSEEK_API_KEY=your_key_here")
        print("     OPENAI_API_KEY=your_key_here")
        print()
        print("  2. Активируйте нужные LLM провайдеры в админ-панели:")
        print("     http://localhost:8000/admin/llmprovider/list")
        print()
        print("  3. Добавьте источники (Source) и привяжите к сценариям:")
        print("     http://localhost:8000/admin/source/list")
        print()
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
