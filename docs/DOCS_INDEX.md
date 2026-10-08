# Индекс документации «ИИ Ассистент»

## Business readiness: current decisions

- **[Integrated roadmap](./ROADMAP_INTEGRATED.md)** — proposed sequence, small PRs and launch gates; reviewed against dev, not a production certification.
- **[Proposal review](./design/proposal_review.md)** — dispositions of all research proposals, code evidence and stale assumptions.
- **[Pre-merge delta recheck](./design/business_readiness_recheck_2026_10_08.md)** — new dev work accounted for before merging the documentation; remaining risks revalidated.
- **[Tenant-safe delivery implementation](./design/tenant_safe_digest_delivery_review.md)** — routing fix, recipient setup compatibility, checks and remaining retry/concurrency work.
- **[Local experience plan](./LOCAL_EXPERIENCE_PLAN.md)** — bounded onboarding, trust, recovery and report improvements; migration boundaries marked.
- **[Business production readiness](./BUSINESS_PRODUCTION_READINESS.md)** — security, cost, delivery, deployment, retention, recovery and support gates.
- **[Future scale strategy](./design/future_scale_strategy.md)** — deferred embeddings, alerts, billing and capacity work with entry criteria.


## Видение и целевая архитектура

- **[Видение продукта](./design/vision.md)** - принципы, гибридный сбор (API + авторизация), промпты, обучение, роадмап, что не делаем

## Справочники

- **[API Reference](./API.md)** - Все REST endpoints, схемы запросов/ответов, аутентификация
- **[CLI Reference](./CLI.md)** - Все команды CLI (task, digest, credentials, roles)
- **[Admin Panel](./ADMIN.md)** - sqladmin страницы, кастомные actions, auth, CSRF
- **[Data Models](./MODELS.md)** - Все таблицы, поля, связи, ER-диаграмма
- **[Configuration](./CONFIGURATION.md)** - Полный список env vars, .env.example, генерация ключей
- **[Channels](./CHANNELS.md)** - Telegram/MAX каналы, listener, ingest, routing
- **[Deployment](./DEPLOYMENT.md)** - Docker Compose, systemd, nginx, бэкапы, first deployment checklist

## Анализ и мониторинг

- **[Типы AI-анализа](AI_PIPELINE_AND_PROMPTS.md)** - AI-конвейер, PromptBuilder, промпты по типам медиа
- **[Агрегация аналитики](./ANALYTICS_AGGREGATION_SYSTEM.md)** - Обработка и хранение данных
- **[Тематические цепочки](./ANALYTICS_CHAINS.md)** - Привязка анализов к цепочкам, relevance-фильтр, группировки, веб и дайджест
- **[Автозаполнение промптов и scope](./PROMPT_AND_SCOPE_EXPLAINED.md)** - Поведение PromptBuilder и json_schema_builder
- **[Сбор контента и креды платформ](./COLLECTION.md)** - Слои сбора (API/сессия/браузер), волт токенов, VK и Telegram Bot API

## Очередь и фоновые задачи

- **[Планировщик и очередь задач](./AGENT_TASKS.md)** - Cron-расписания и фоновые джобы на PostgreSQL
- **[Доставка дайджестов](./DIGEST.md)** - Сводки, каналы Telegram/MAX, идемпотентность
- **[Агент в чате](./AGENT.md)** - Tool-calling runtime, сессии, память, подтверждения
- **[Чат-создание сценариев](./CHAT_BOT_SCENARIOS.md)** - Архитектура сценариев (линза vs реакция), агент-тулы, шаблоны, UX-паттерны

## Инфраструктура

- **[Мультитенантность](./TENANCY.md)** - Рабочие пространства, инвайт-коды, изоляция данных
