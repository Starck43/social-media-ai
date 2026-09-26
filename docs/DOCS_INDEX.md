# Индекс документации Social Media AI

## Видение и целевая архитектура

- **[Видение продукта](./design/vision.md)** - принципы, гибридный сбор (API + авторизация), промпты, обучение, роадмап, что не делаем

## Справочники

- **[API Reference](./API.md)** - Все REST endpoints, схемы запросов/ответов, аутентификация
- **[CLI Reference](./CLI.md)** - Все команды CLI (schedule, digest, credentials, roles)
- **[Admin Panel](./ADMIN.md)** - sqladmin страницы, кастомные actions, auth, CSRF
- **[Data Models](./MODELS.md)** - Все таблицы, поля, связи, ER-диаграмма
- **[Configuration](./CONFIGURATION.md)** - Полный список env vars, .env.example, генерация ключей
- **[Channels](./CHANNELS.md)** - Telegram/MAX каналы, listener, ingest, routing
- **[Deployment](./DEPLOYMENT.md)** - Docker Compose, systemd, nginx, бэкапы, first deployment checklist

## Анализ и мониторинг

- **[Типы AI-анализа](./guide/AI_PIPELINE_AND_PROMPTS.md)** - AI-конвейер, PromptBuilder, промпты по типам медиа
- **[Агрегация аналитики](./ANALYTICS_AGGREGATION_SYSTEM.md)** - Обработка и хранение данных
- **[Автозаполнение промптов и scope](./PROMPT_AND_SCOPE_EXPLAINED.md)** - Поведение PromptBuilder и json_schema_builder
- **[Сбор контента и креды платформ](./COLLECTION.md)** - Слои сбора (API/сессия/браузер), волт токенов, VK и Telegram Bot API

## Очередь и фоновые задачи

- **[Планировщик и очередь задач](./AGENT_TASKS.md)** - Cron-расписания и фоновые джобы на PostgreSQL
- **[Доставка дайджестов](./DIGEST.md)** - Сводки, каналы Telegram/MAX, идемпотентность
- **[Агент в чате](./AGENT.md)** - Tool-calling runtime, сессии, память, подтверждения

## Инфраструктура

- **[Мультитенантность](./TENANCY.md)** - Рабочие пространства, инвайт-коды, изоляция данных
