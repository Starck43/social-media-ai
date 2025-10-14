-- =====================================================================
-- Seed Script: LLM Providers и Bot Scenarios для тестирования
-- =====================================================================
-- Использование: psql -U your_user -d your_db -f scripts/seed_llm_and_scenarios.sql
-- =====================================================================

-- Очистка существующих тестовых данных (опционально)
-- DELETE FROM social_manager.bot_scenarios WHERE name LIKE '%[TEST]%';
-- DELETE FROM social_manager.llm_providers WHERE name LIKE '%[TEST]%';

-- =====================================================================
-- LLM PROVIDERS (бесплатные и популярные модели)
-- =====================================================================

-- 1. DeepSeek (уже должен быть создан миграцией, но добавим на всякий случай)
INSERT INTO social_manager.llm_providers 
    (name, description, provider_type, api_url, api_key_env, model_name, capabilities, config, is_active)
VALUES 
    (
        'DeepSeek Chat',
        'DeepSeek LLM для текстового анализа. Быстрый и доступный по цене.',
        'deepseek',
        'https://api.deepseek.com/v1/chat/completions',
        'DEEPSEEK_API_KEY',
        'deepseek-chat',
        '["text"]'::jsonb,
        '{"temperature": 0.2, "max_tokens": 2000}'::jsonb,
        true
    )
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    is_active = EXCLUDED.is_active;

-- 2. OpenAI GPT-3.5 (для текста)
INSERT INTO social_manager.llm_providers 
    (name, description, provider_type, api_url, api_key_env, model_name, capabilities, config, is_active)
VALUES 
    (
        'OpenAI GPT-3.5 Turbo',
        'OpenAI GPT-3.5 Turbo для быстрого анализа текста. Доступная цена.',
        'openai',
        'https://api.openai.com/v1/chat/completions',
        'OPENAI_API_KEY',
        'gpt-3.5-turbo',
        '["text"]'::jsonb,
        '{"temperature": 0.3, "max_tokens": 2000}'::jsonb,
        false  -- По умолчанию неактивен (нужен ключ)
    )
ON CONFLICT (name) DO NOTHING;

-- 3. OpenAI GPT-4 (для текста и изображений)
INSERT INTO social_manager.llm_providers 
    (name, description, provider_type, api_url, api_key_env, model_name, capabilities, config, is_active)
VALUES 
    (
        'OpenAI GPT-4 Turbo',
        'OpenAI GPT-4 Turbo для комплексного анализа текста. Высокое качество.',
        'openai',
        'https://api.openai.com/v1/chat/completions',
        'OPENAI_API_KEY',
        'gpt-4-turbo-preview',
        '["text"]'::jsonb,
        '{"temperature": 0.2, "max_tokens": 3000}'::jsonb,
        false  -- По умолчанию неактивен (нужен ключ)
    )
ON CONFLICT (name) DO NOTHING;

-- 4. OpenAI GPT-4 Vision (для изображений и видео)
INSERT INTO social_manager.llm_providers 
    (name, description, provider_type, api_url, api_key_env, model_name, capabilities, config, is_active)
VALUES 
    (
        'OpenAI GPT-4 Vision',
        'OpenAI GPT-4 с поддержкой анализа изображений и видео.',
        'openai',
        'https://api.openai.com/v1/chat/completions',
        'OPENAI_API_KEY',
        'gpt-4-vision-preview',
        '["text", "image", "video"]'::jsonb,
        '{"temperature": 0.1, "max_tokens": 3000}'::jsonb,
        false  -- По умолчанию неактивен (нужен ключ)
    )
ON CONFLICT (name) DO NOTHING;

-- 5. Anthropic Claude (альтернатива)
INSERT INTO social_manager.llm_providers 
    (name, description, provider_type, api_url, api_key_env, model_name, capabilities, config, is_active)
VALUES 
    (
        'Anthropic Claude 3',
        'Anthropic Claude 3 для анализа текста. Хорошо понимает контекст.',
        'anthropic',
        'https://api.anthropic.com/v1/messages',
        'ANTHROPIC_API_KEY',
        'claude-3-opus-20240229',
        '["text"]'::jsonb,
        '{"temperature": 0.2, "max_tokens": 2000}'::jsonb,
        false  -- По умолчанию неактивен (нужен ключ)
    )
ON CONFLICT (name) DO NOTHING;

-- =====================================================================
-- BOT SCENARIOS (готовые сценарии на русском)
-- =====================================================================

-- Получаем ID DeepSeek провайдера для использования в сценариях
DO $$
DECLARE
    deepseek_id INTEGER;
    gpt4v_id INTEGER;
BEGIN
    -- Получаем ID провайдеров
    SELECT id INTO deepseek_id FROM social_manager.llm_providers WHERE name = 'DeepSeek Chat' LIMIT 1;
    SELECT id INTO gpt4v_id FROM social_manager.llm_providers WHERE name = 'OpenAI GPT-4 Vision' LIMIT 1;
    
    -- Если не нашли GPT-4 Vision, используем DeepSeek для всего
    IF gpt4v_id IS NULL THEN
        gpt4v_id := deepseek_id;
    END IF;

    -- 1. Анализ настроений клиентов
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes, 
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Анализ настроений клиентов',
            'Отслеживание эмоционального настроя аудитории, выявление позитивных и негативных тенденций',
            '["posts", "comments"]'::jsonb,
            '["sentiment", "topics"]'::jsonb,
            '{
                "sentiment_config": {
                    "categories": ["positive", "negative", "neutral"],
                    "track_emotions": true
                },
                "topics_config": {
                    "max_topics": 5
                }
            }'::jsonb,
            'Проанализируй настроение аудитории в следующем контенте из {platform}:

КОНТЕНТ:
{content}

Определи:
1. Общий эмоциональный настрой (позитивный/негативный/нейтральный)
2. Преобладающие эмоции (радость, разочарование, гнев, интерес и т.д.)
3. Основные темы, вызывающие позитивную реакцию
4. Основные темы, вызывающие негативную реакцию
5. Ключевые фразы и слова, которые используют пользователи

Верни результат в JSON формате с полями: overall_sentiment, dominant_emotions, positive_topics, negative_topics, key_phrases',
            'notification',
            true,
            60,
            deepseek_id,
            NULL,
            NULL
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt,
        text_llm_provider_id = EXCLUDED.text_llm_provider_id;

    -- 2. Мониторинг упоминаний бренда
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes,
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Мониторинг упоминаний бренда',
            'Отслеживание упоминаний бренда/продукта, анализ контекста и тональности',
            '["posts", "comments", "mentions"]'::jsonb,
            '["sentiment", "keywords", "brand_mentions"]'::jsonb,
            '{
                "keywords_config": {
                    "track_keywords": true,
                    "min_mentions": 2
                },
                "brand_mentions_config": {
                    "context_analysis": true
                }
            }'::jsonb,
            'Проанализируй упоминания бренда/продукта в контенте из {platform}:

КОНТЕНТ:
{content}

Для каждого упоминания определи:
1. Контекст упоминания (положительный/отрицательный/нейтральный/вопрос)
2. Связанные темы и проблемы
3. Есть ли сравнение с конкурентами
4. Типичные вопросы и проблемы пользователей
5. Возможности для улучшения репутации

Верни результат в JSON формате с полями: brand_mentions, sentiment_distribution, common_issues, competitor_comparisons, improvement_opportunities',
            'notification',
            true,
            30,
            deepseek_id,
            NULL,
            NULL
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt;

    -- 3. Отслеживание трендов
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes,
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Отслеживание трендов',
            'Выявление новых тем, вирусного контента и растущих интересов аудитории',
            '["posts", "reactions"]'::jsonb,
            '["trends", "viral_detection", "topics"]'::jsonb,
            '{
                "trends_config": {
                    "min_mentions": 3,
                    "time_window": "24h"
                },
                "viral_detection_config": {
                    "engagement_threshold": 100
                }
            }'::jsonb,
            'Проанализируй контент из {platform} и найди тренды:

КОНТЕНТ ({total_posts} постов):
{content}

Определи:
1. Новые темы, которые набирают популярность
2. Вирусный контент (посты с высокой вовлеченностью)
3. Изменения в интересах аудитории по сравнению с прошлым
4. Хештеги и фразы, которые активно используются
5. Прогноз: какие темы будут актуальны в ближайшее время

Верни результат в JSON формате с полями: emerging_trends, viral_content, trending_hashtags, audience_interests, trend_forecast',
            'notification',
            true,
            120,
            deepseek_id,
            NULL,
            NULL
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt;

    -- 4. Анализ конкурентов
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes,
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Анализ конкурентов',
            'Мониторинг активности конкурентов, их стратегии и реакции аудитории',
            '["posts", "comments", "reactions"]'::jsonb,
            '["competitor_tracking", "engagement", "topics"]'::jsonb,
            '{
                "competitor_tracking_config": {
                    "compare_metrics": true,
                    "track_campaigns": true
                }
            }'::jsonb,
            'Проанализируй активность конкурента в {platform}:

КОНТЕНТ:
{content}

Определи:
1. Основные темы и форматы контента конкурента
2. Что работает хорошо (высокая вовлеченность)
3. Что работает плохо (низкая вовлеченность)
4. Частота публикаций и оптимальное время
5. Уникальные подходы и фишки
6. Слабые стороны, которые можно использовать

Верни результат в JSON формате с полями: content_strategy, best_performing, worst_performing, posting_pattern, unique_approaches, opportunities',
            'notification',
            true,
            180,
            deepseek_id,
            NULL,
            NULL
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt;

    -- 5. Мониторинг отзывов и обратной связи
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes,
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Мониторинг отзывов',
            'Анализ отзывов о продукте/услуге, выявление проблем и возможностей улучшения',
            '["posts", "comments"]'::jsonb,
            '["sentiment", "customer_intent", "topics"]'::jsonb,
            '{
                "customer_intent_config": {
                    "detect_complaints": true,
                    "detect_questions": true,
                    "detect_praise": true
                }
            }'::jsonb,
            'Проанализируй отзывы и обратную связь из {platform}:

КОНТЕНТ:
{content}

Раздели отзывы на категории:
1. ЖАЛОБЫ: Какие проблемы и недовольства упоминаются
2. ВОПРОСЫ: Какая информация нужна клиентам
3. ПОХВАЛЫ: Что нравится пользователям
4. ПРЕДЛОЖЕНИЯ: Идеи по улучшению от клиентов

Для каждой категории:
- Подсчитай количество упоминаний
- Выдели ключевые темы
- Оцени срочность/важность
- Дай рекомендации по реакции

Верни результат в JSON формате с полями: complaints, questions, praise, suggestions, priority_issues, recommendations',
            'notification',
            true,
            60,
            deepseek_id,
            NULL,
            NULL
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt;

    -- 6. Анализ визуального контента
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes,
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Анализ визуального контента',
            'Анализ изображений и видео: что изображено, тональность, брендинг',
            '["posts", "videos", "stories"]'::jsonb,
            '["sentiment", "topics"]'::jsonb,
            '{}'::jsonb,
            'Проанализируй визуальный контент (фото/видео) из {platform}:

Для каждого изображения/видео определи:
1. Основные объекты и сюжет
2. Эмоциональная окраска (радость, грусть, нейтрально)
3. Есть ли элементы брендинга (логотипы, фирменные цвета)
4. Качество контента (профессиональное/любительское)
5. Вовлекающие элементы (лица людей, яркие цвета, необычные ракурсы)

Верни результат в JSON формате с полями: visual_themes, emotional_tone, brand_presence, content_quality, engagement_factors',
            'notification',
            true,
            90,
            deepseek_id,
            gpt4v_id,
            gpt4v_id
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt,
        image_llm_provider_id = EXCLUDED.image_llm_provider_id,
        video_llm_provider_id = EXCLUDED.video_llm_provider_id;

    -- 7. Экспресс-анализ (быстрый)
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes,
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Экспресс-анализ',
            'Быстрый обзор: основные темы, настроение, активность (для ежечасного мониторинга)',
            '["posts"]'::jsonb,
            '["sentiment", "topics"]'::jsonb,
            '{
                "topics_config": {
                    "max_topics": 3
                }
            }'::jsonb,
            'Сделай быстрый анализ контента из {platform} за последний период:

КОНТЕНТ ({total_posts} постов):
{content}

Кратко опиши (2-3 предложения):
1. О чём говорят (главные темы)
2. Как говорят (общее настроение)
3. Что выделяется (необычное или важное)

Верни результат в JSON формате с полями: main_topics, overall_mood, highlights',
            NULL,
            true,
            15,
            deepseek_id,
            NULL,
            NULL
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt;

    -- 8. Полный комплексный анализ (все типы контента)
    INSERT INTO social_manager.bot_scenarios 
        (name, description, content_types, analysis_types, scope, ai_prompt, action_type, is_active, cooldown_minutes,
         text_llm_provider_id, image_llm_provider_id, video_llm_provider_id)
    VALUES 
        (
            'Полный комплексный анализ',
            'Глубокий анализ всех типов контента (текст, фото, видео) со всеми метриками',
            '["posts", "comments", "videos", "reactions"]'::jsonb,
            '["sentiment", "topics", "engagement", "keywords", "trends"]'::jsonb,
            '{
                "sentiment_config": {
                    "categories": ["positive", "negative", "neutral", "mixed"],
                    "track_emotions": true
                },
                "topics_config": {
                    "max_topics": 10
                },
                "engagement_config": {
                    "metrics": ["likes", "comments", "shares", "views"]
                },
                "trends_config": {
                    "min_mentions": 3
                }
            }'::jsonb,
            'Выполни комплексный глубокий анализ всего контента из {platform}:

ДАННЫЕ:
- Всего постов: {total_posts}
- Период: {date_range}
- Платформа: {platform}

КОНТЕНТ:
{content}

ЗАДАЧИ АНАЛИЗА:

1. SENTIMENT ANALYSIS (Анализ тональности):
   - Общее настроение аудитории
   - Распределение эмоций
   - Динамика изменения настроения

2. TOPIC ANALYSIS (Анализ тем):
   - Топ-10 обсуждаемых тем
   - Новые темы (emerging topics)
   - Угасающие темы

3. ENGAGEMENT ANALYSIS (Анализ вовлеченности):
   - Какой контент набирает больше реакций
   - Оптимальное время публикации
   - Форматы с лучшей вовлеченностью

4. CONTENT ANALYSIS (Анализ контента):
   - Качество контента
   - Ключевые фразы и хештеги
   - Типы контента (текст, фото, видео)

5. AUDIENCE INSIGHTS (Инсайты аудитории):
   - О чём беспокоится аудитория
   - Что интересует аудиторию
   - Предложения и пожелания

6. RECOMMENDATIONS (Рекомендации):
   - Что делать для улучшения вовлеченности
   - На какие темы стоит обратить внимание
   - Какие проблемы требуют решения

Верни подробный анализ в JSON формате.',
            'notification',
            true,
            240,
            deepseek_id,
            gpt4v_id,
            gpt4v_id
        )
    ON CONFLICT (name) DO UPDATE SET
        description = EXCLUDED.description,
        ai_prompt = EXCLUDED.ai_prompt,
        text_llm_provider_id = EXCLUDED.text_llm_provider_id,
        image_llm_provider_id = EXCLUDED.image_llm_provider_id,
        video_llm_provider_id = EXCLUDED.video_llm_provider_id;

END $$;

-- =====================================================================
-- ИТОГОВАЯ ИНФОРМАЦИЯ
-- =====================================================================

-- Выводим список созданных провайдеров
SELECT 
    id,
    name,
    provider_type,
    model_name,
    capabilities,
    is_active,
    CASE 
        WHEN is_active THEN '✅ Активен'
        ELSE '❌ Неактивен (добавьте API ключ в .env)'
    END as status
FROM social_manager.llm_providers
ORDER BY id;

-- Выводим список созданных сценариев
SELECT 
    id,
    name,
    description,
    CASE 
        WHEN is_active THEN '✅ Активен'
        ELSE '❌ Неактивен'
    END as status,
    cooldown_minutes as "Интервал (мин)"
FROM social_manager.bot_scenarios
ORDER BY id;

-- Информация о сценариях с LLM провайдерами
SELECT 
    bs.name as "Сценарий",
    tp.name as "LLM для текста",
    ip.name as "LLM для фото",
    vp.name as "LLM для видео"
FROM social_manager.bot_scenarios bs
LEFT JOIN social_manager.llm_providers tp ON bs.text_llm_provider_id = tp.id
LEFT JOIN social_manager.llm_providers ip ON bs.image_llm_provider_id = ip.id
LEFT JOIN social_manager.llm_providers vp ON bs.video_llm_provider_id = vp.id
ORDER BY bs.id;
