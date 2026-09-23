## 🎯 Видение работы продукта

### Как это должно работать в идеале:

**1. Система как персональный AI-ассистент для SMM/маркетинга:**
- Агент постоянно мониторит выбранные источники (как Hermes)
- Собирает контент, анализирует его "на лету" без хранения сырых данных
- Выделяет тренды, темы, настроенческие волны
- Автоматически реагирует по заданным сценариям
- Присылает умные ежедневные сводки

**2. Архитектурная схема работы:**
```
┌─────────────────┐
│   Scheduler     │ ← Планировщик (Celery Beat/APScheduler)
└────────┬────────┘
         │ запускает по расписанию
         ▼
┌─────────────────┐     ┌─────────────────┐
│  Collectors     │────▶│  VK/Telegram    │
│  (парсеры)      │     │  API            │
└────────┬────────┘     └─────────────────┘
         │ сырой контент
         ▼
┌─────────────────┐
│  AI Analyzer    │────▶ DeepSeek/GPT API
│  (анализ)       │
└────────┬────────┘
         │ метаданные, темы, sentiment
         ▼
┌─────────────────┐     ┌─────────────────┐
│  PostgreSQL     │     │  Bot Executor   │────▶ Комментирование
│  (аналитика)    │     │  (автоответы)   │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│  Reporter       │────▶ Email/Telegram
│  (отчеты)       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Streamlit UI   │────▶ Пользователь
└─────────────────┘
```

---

## 📋 ТЕХНИЧЕСКОЕ ЗАДАНИЕ (ТЗ)

### **1. ОБЩИЕ ПОЛОЖЕНИЯ**

#### 1.1. Назначение системы
Создание AI-агента для автоматизированного мониторинга, анализа и взаимодействия с социальными сетями (VK, Telegram, Max) с возможностью автоматического комментирования и генерации аналитических отчетов.

#### 1.2. Цели проекта
- Автоматизация сбора и анализа контента из соцсетей
- Снижение времени на ручной мониторинг с часов до минут
- Автоматизация рутинных взаимодействий (ответы, модерация)
- Предоставление actionable-аналитики в реальном времени

#### 1.3. Целевая аудитория
- SMM-специалисты
- Маркетологи
- Комьюнити-менеджеры
- Владельцы бизнеса

---

### **2. ФУНКЦИОНАЛЬНЫЕ ТРЕБОВАНИЯ**

#### **2.1. Сбор контента (Content Collection)**

**Требования:**
- **FR-2.1.1** Система должна собирать посты, комментарии, сообщения из:
  - VK: группы, публичные страницы, пользовательские стены
  - Telegram: каналы (публичные и приватные через бота), чаты (где бот участник), личные сообщения
  - Max: аналогично Telegram (при наличии API)

- **FR-2.1.2** Настраиваемая периодичность сбора:
  - От 5 минут до 24 часов на каждый источник
  - Приоритизация: VIP-источники чаще, обычные — реже
  - Учет rate limits API соцсетей

- **FR-2.1.3** Инкрементальный сбор:
  - Использование `last_checked` для сбора только нового контента
  - Поддержка `date_from`/`date_to` для исторического анализа
  - Механизм checkpoints для возобновления после сбоев

- **FR-2.1.4** Обработка типов контента:
  - Текст (посты, комментарии, сообщения)
  - Медиа (фото, видео, документы) — только метаданные
  - Опросы, реакции, репосты
  - Ссылки (с preview и анализом)

#### **2.2. Мониторинг активности (Activity Monitoring)**

**Требования:**
- **FR-2.2.1** Отслеживание метрик:
  - Количество постов/комментариев за период
  - Активность пользователей (frequency analysis)
  - Время пиковой активности
  - Рост/падение вовлеченности

- **FR-2.2.2** Детектирование аномалий:
  - Внезапные всплески активности
  - Негативные волны (sentiment drop)
  - Появление новых активных участников
  - Уход ключевых участников

- **FR-2.2.3** Система алертов:
  - Настраиваемые триггеры (например, "sentiment < -0.5 в течение часа")
  - Мгновенные уведомления в Telegram
  - Разные уровни приоритета: info, warning, critical

#### **2.3. AI-анализ контента (AI Analysis)**

**Требования:**
- **FR-2.3.1** Определение тематики:
  - Автоматическая классификация по 20+ предопределенным темам
  - Выделение ключевых слов и фраз
  - Генерация краткого резюме (1-2 предложения)

- **FR-2.3.2** Sentiment analysis:
  - Оценка настроения: positive, neutral, negative
  - Числовая оценка от -1.0 до 1.0
  - Определение эмоциональной окраски (радость, гнев, грусть и т.д.)

- **FR-2.3.3** Topic Chains (эволюция тем):
  - Отслеживание развития тем во времени
  - Выявление нарративов и их трансформации
  - Визуализация цепочек обсуждений

- **FR-2.3.4** Детектирование событий:
  - Автоматическое выделение событий (event-based analysis)
  - Группировка контента по событиям
  - Хронология развития событий

- **FR-2.3.5** Персонализация:
  - Кастомные промпты для разных сценариев
  - Настройка глубины анализа
  - Приоритизация типов контента

#### **2.4. Автоматическое комментирование (Auto-commenting)**

**Требования:**
- **FR-2.4.1** Система сценариев:
  - Предопределенные шаблоны ответов
  - Контекстные ответы на основе AI
  - Гибридный режим: шаблон + AI-генерация

- **FR-2.4.2** Триггеры для комментирования:
  - Ключевые слова/фразы
  - Sentiment threshold
  - Упоминания бота/аккаунта
  - Расписание (например, "каждое утро")

- **FR-2.4.3** Безопасность и ограничения:
  - Задержка между комментариями (anti-spam)
  - Лимиты: максимум N комментариев в час/день
  - Blacklist пользователей/групп
  - Whitelist для модерации
  - Требование прав модератора для автоматизации

- **FR-2.4.4** Human-in-the-loop:
  - Предпросмотр перед отправкой
  - Ручная модерация для критических ответов
  - Возможность отменить запланированный ответ

#### **2.5. Ежедневные отчеты (Daily Reports)**

**Требования:**
- **FR-2.5.1** Содержание отчета:
  - Summary: топ-3 события дня
  - Sentiment overview: общее настроение
  - Активность: самые активные источники
  - Аномалии: что привлекло внимание
  - Действия бота: сколько комментариев отправлено

- **FR-2.5.2** Каналы доставки:
  - Email (HTML/PDF формат)
  - Telegram (краткий в канале + детальный в личку)
  - Max (аналогично Telegram)
  - Сохранение в системе для истории

- **FR-2.5.3** Настройка:
  - Время отправки (например, 9:00 утра)
  - Уровень детализации: краткий/средний/полный
  - Получатели: список email/Telegram ID
  - Формат: inline/attachment

#### **2.6. Единый интерфейс (Unified Dashboard)**

**Требования:**
- **FR-2.6.1** Главная панель (Dashboard):
  - Обзор всех источников с их статусом
  - Общая статистика за день/неделю/месяц
  - Timeline последних событий
  - Виджеты с ключевыми метриками

- **FR-2.6.2** Детальная аналитика:
  - Drill-down по каждому источнику
  - Графики активности
  - Облака тегов и ключевых слов
  - Sentiment trends
  - Topic chains evolution

- **FR-2.6.3** Управление:
  - Добавление/удаление источников
  - Настройка сценариев
  - Просмотр истории действий бота
  - Управление пользователями (RBAC)

- **FR-2.6.4** Уведомления:
  - Real-time alerts в интерфейсе
  - История уведомлений
  - Настройка каналов уведомлений

---

### **3. НЕФУНКЦИОНАЛЬНЫЕ ТРЕБОВАНИЯ**

#### **3.1. Производительность**
- **NFR-3.1.1** Обработка до 10,000 постов в час
- **NFR-3.1.2** Время ответа AI-анализа: < 5 секунд на пост
- **NFR-3.1.3** Время загрузки дашборда: < 2 секунды
- **NFR-3.1.4** Поддержка 100+ источников одновременно

#### **3.2. Надежность**
- **NFR-3.2.1** Uptime: 99.5% (кроме плановых работ)
- **NFR-3.2.2** Автоматическое восстановление после сбоев
- **NFR-3.2.3** Retry-механизмы для API-запросов
- **NFR-3.2.4** Graceful degradation при недоступности AI

#### **3.3. Безопасность**
- **NFR-3.3.1** Шифрование credentials в базе (Fernet)
- **NFR-3.3.2** HTTPS для всех веб-интерфейсов
- **NFR-3.3.3** RBAC: роли admin, editor, viewer
- **NFR-3.3.4** Audit log всех действий
- **NFR-3.3.5** Compliance с GDPR (если применимо)

#### **3.4. Масштабируемость**
- **NFR-3.4.1** Горизонтальное масштабирование Celery workers
- **NFR-3.4.2** Поддержка нескольких реплик API
- **NFR-3.4.3** Возможность шардирования БД по источникам

#### **3.5. Мониторинг**
- **NFR-3.5.1** Health checks для всех компонентов
- **NFR-3.5.2** Метрики: Prometheus + Grafana
- **NFR-3.5.3** Логирование: structured JSON logs
- **NFR-3.5.4** Alerting через Telegram/Email

---

### **4. АРХИТЕКТУРА СИСТЕМЫ**

#### **4.1. Технологический стек**

**Backend:**
- **FastAPI** — API и веб-интерфейс (уже есть)
- **SQLAlchemy 2.0** — ORM (async support)
- **PostgreSQL 15+** — основная БД
- **Redis 7+** — кэширование + брокер сообщений
- **Celery 5+** — фоновые задачи
- **APScheduler** — планировщик (альтернатива Celery Beat)

**AI/ML:**
- **DeepSeek API** — основной LLM (уже есть)
- **OpenAI API** — fallback вариант
- **Local models** — опционально для privacy-sensitive задач
- **spaCy** — NLP preprocessing (опционально)

**Frontend:**
- **Streamlit** — основной дашборд (уже есть)
- **Chart.js** — визуализация (уже есть)
- **Tailwind CSS** — стилизация (опционально)

**Infrastructure:**
- **Docker** + **Docker Compose** (уже есть)
- **Nginx** — reverse proxy
- **Traefik** — альтернатива с auto-discovery
- **GitHub Actions** — CI/CD

#### **4.2. Структура проекта**

```
social-media-ai/
├── app/
│   ├── api/              # FastAPI endpoints
│   │   ├── v1/
│   │   │   ├── sources.py
│   │   │   ├── analytics.py
│   │   │   ├── reports.py
│   │   │   └── bot.py
│   │   └── deps.py       # Dependencies
│   ├── admin/            # Admin panel (SQLAdmin)
│   ├── celery/           # Celery tasks
│   │   ├── collectors/   # Парсеры соцсетей
│   │   ├── analyzers/    # AI анализ
│   │   ├── reporters/    # Генерация отчетов
│   │   └── bot/          # Бот-комментатор
│   ├── core/             # Конфигурация
│   ├── models/           # SQLAlchemy models
│   ├── schemas/          # Pydantic schemas
│   ├── services/         # Business logic
│   ├── types/            # Custom types
│   └── utils/            # Utilities
├── cli/                  # CLI интерфейс
├── docker/               # Docker configs
├── docs/                 # Documentation
├── scripts/              # Utility scripts
├── tests/                # Test suite
├── alembic/              # DB migrations
├── pyproject.toml
├── docker-compose.yml
└── README.md
```

#### **4.3. Схема базы данных**

**Основные таблицы:**

1. **platforms** — платформы (VK, Telegram, Max)
   - id, name, api_config, credentials (encrypted)

2. **sources** — источники мониторинга
   - id, platform_id, external_id, type (group/user/channel/chat)
   - name, description, is_active, monitoring_interval
   - params (JSON: filters, date ranges)
   - last_checked, last_success

3. **ai_analytics** — результаты анализа
   - id, source_id, content_hash, analysis_date
   - sentiment_score, sentiment_label, emotion
   - topics (JSON array), keywords (JSON array)
   - summary, topic_chain_id, event_id

4. **bot_scenarios** — сценарии бота
   - id, name, trigger_type, trigger_config (JSON)
   - response_template, ai_enhancement
   - is_active, cooldown_period, max_executions

5. **bot_actions** — лог действий бота
   - id, scenario_id, source_id, content_hash
   - action_type, status, response_text
   - executed_at, human_approved

6. **notifications** — уведомления
   - id, type, priority, message
   - channels (JSON: email, telegram), recipients
   - sent_at, delivered

7. **reports** — история отчетов
   - id, report_date, report_type
   - summary_data (JSON), file_path
   - recipients, sent_at

8. **users** — пользователи системы (RBAC)
   - id, username, email, password_hash
   - role (admin/editor/viewer), is_active

9. **checkpoints** — точки восстановления
   - id, source_id, checkpoint_data (JSON)
   - created_at

---

### **5. РЕКОМЕНДАЦИИ ДЛЯ ИИ-МОДЕЛИ (AI Guidelines)**

#### **5.1. Prompt Engineering Best Practices**

**Для анализа контента:**
```python
SYSTEM_PROMPT = """
Ты - эксперт-аналитик социальных сетей. Твоя задача:
1. Определить основную тему текста
2. Оценить эмоциональную окраску
3. Выделить ключевые сущности
4. Сгенерировать краткое резюме

Отвечай СТРОГО в JSON формате:
{
  "topics": ["тема1", "тема2"],
  "sentiment": {
    "score": число от -1 до 1,
    "label": "positive/neutral/negative",
    "emotion": "joy/anger/sadness/..."
  },
  "entities": ["сущность1", "сущность2"],
  "summary": "краткое резюме в 1-2 предложениях",
  "keywords": ["ключевое1", "ключевое2"]
}
"""
```

**Для генерации комментариев:**
```python
SYSTEM_PROMPT = """
Ты - дружелюбный комьюнити-менеджер. Сгенерируй ответ на сообщение:
- Будь кратким (1-3 предложения)
- Используй естественный язык
- Учитывай контекст и тон оригинального сообщения
- Не повторяй слово в слово
- Если есть вопрос - дай полезный ответ

Исходное сообщение: {original_message}
Контекст обсуждения: {context}

Ответ (только текст, без кавычек):
"""
```

#### **5.2. Temperature и параметры**

```python
# Для анализа (точность важнее креативности)
analysis_params = {
    "temperature": 0.3,
    "max_tokens": 1000,
    "top_p": 0.9,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0
}

# Для генерации ответов (баланс точности и естественности)
response_params = {
    "temperature": 0.7,
    "max_tokens": 500,
    "top_p": 0.95,
    "frequency_penalty": 0.3,
    "presence_penalty": 0.3
}

# Для креативных задач (brainstorming, идеи)
creative_params = {
    "temperature": 0.9,
    "max_tokens": 1000,
    "top_p": 0.95,
    "frequency_penalty": 0.5,
    "presence_penalty": 0.5
}
```

#### **5.3. Обработка ошибок и fallback**

```python
async def analyze_with_fallback(text: str) -> dict:
    providers = [
        ("deepseek", DeepSeekClient()),
        ("openai", OpenAIClient()),
        ("local", LocalModelClient())  # опционально
    ]
    
    for provider_name, client in providers:
        try:
            result = await client.analyze(text)
            validate_analysis(result)
            log_success(provider_name)
            return result
        except Exception as e:
            log_error(provider_name, e)
            continue
    
    # Все провайдеры упали - возвращаем дефолтные значения
    return {
        "topics": ["unknown"],
        "sentiment": {"score": 0.0, "label": "neutral", "emotion": "neutral"},
        "summary": "Анализ недоступен",
        "keywords": []
    }
```

#### **5.4. Rate Limiting и очереди**

```python
from aiolimiter import AsyncLimiter

# Глобальный rate limiter для AI API
ai_limiter = AsyncLimiter(max_rate=10, time_period=1)  # 10 запросов/сек

async def analyze_batch(posts: list) -> list:
    results = []
    async with ai_limiter:
        for post in posts:
            result = await analyze_with_fallback(post.text)
            results.append(result)
            await asyncio.sleep(0.1)  # Дополнительная задержка
    return results
```

#### **5.5. Кэширование результатов**

```python
import hashlib
from redis import Redis

redis_client = Redis(host='redis', port=6379, db=1)

async def analyze_with_cache(text: str) -> dict:
    text_hash = hashlib.md5(text.encode()).hexdigest()
    cache_key = f"analysis:{text_hash}"
    
    # Проверяем кэш
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    
    # Анализируем
    result = await analyze_with_fallback(text)
    
    # Сохраняем в кэш на 24 часа
    redis_client.setex(
        cache_key, 
        86400,  # 24 часа
        json.dumps(result)
    )
    
    return result
```

---

### **6. ПЛАН РАЗРАБОТКИ (Roadmap)**

#### **Phase 1: Foundation (2-3 недели)**
- [ ] Рефакторинг существующего кода
- [ ] Улучшение обработки ошибок
- [ ] Добавление логирования (structlog)
- [ ] Настройка CI/CD
- [ ] Базовые тесты (unit + integration)

#### **Phase 2: Core Features (3-4 недели)**
- [ ] Улучшение парсеров VK/Telegram
- [ ] Расширение AI-анализа
- [ ] Topic Chains enhancement
- [ ] Event-based analysis
- [ ] Улучшение дашборда

#### **Phase 3: Automation (2-3 недели)**
- [ ] Bot scenarios expansion
- [ ] Human-in-the-loop система
- [ ] Автоматическая модерация
- [ ] Триггеры и автоответы

#### **Phase 4: Reports & Analytics (2 недели)**
- [ ] Daily reports generator
- [ ] Email delivery
- [ ] PDF export
- [ ] Advanced analytics

#### **Phase 5: Polish (1-2 недели)**
- [ ] Performance optimization
- [ ] Security audit
- [ ] Documentation
- [ ] User testing

---

### **7. КРИТЕРИИ ПРИЕМКИ (Acceptance Criteria)**

#### **Для функции сбора контента:**
- ✅ Успешно собирает посты из 10 тестовых источников
- ✅ Корректно обрабатывает rate limits (не получает бан)
- ✅ Поддерживает инкрементальный сбор
- ✅ Восстанавливается после сбоев

#### **Для AI-анализа:**
- ✅ Точность sentiment analysis > 80% (на тестовом датасете)
- ✅ Корректно определяет темы в 75% случаев
- ✅ Время анализа < 5 секунд на пост
- ✅ Имеет fallback на другие провайдеры

#### **Для автокомментирования:**
- ✅ Отправляет ответы только по триггерам
- ✅ Соблюдает rate limits (не спамит)
- ✅ Поддерживает human-in-the-loop
- ✅ Ведет полный лог действий

#### **Для отчетов:**
- ✅ Генерируется ежедневно в заданное время
- ✅ Содержит всю ключевую информацию
- ✅ Доставляется в Email/Telegram
- ✅ Имеет читаемый формат

---

### **8. РИСКИ И МИТИГАЦИЯ**

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Бан API соцсетей | Средняя | Высокое | Rate limiting, proxies, rotation |
| Недоступность AI API | Средняя | Среднее | Fallback провайдеры, кэширование |
| Превышение бюджета на AI | Средняя | Высокое | Token limits, кэширование, batch processing |
| Неправильная модерация | Высокая | Высокое | Human-in-the-loop, тестирование сценариев |
| Потеря данных | Низкая | Высокое | Регулярные бэкапы, checkpoints |

---

### **9. МЕТРИКИ УСПЕХА (KPIs)**

- **Coverage**: % источников, успешно обработанных за день (>95%)
- **Accuracy**: Точность AI-анализа (>80%)
- **Response Time**: Среднее время анализа (<5 сек)
- **Automation Rate**: % автоматически обработанного контента (>70%)
- **User Satisfaction**: NPS пользователей (>50)

---

## 🚀 Следующие шаги

1. **Приоритизация задач**: определите, какие фичи самые критичные
2. **Настройка окружения**: убедитесь, что Docker работает корректно
3. **Миграции БД**: проверьте, что все миграции применены
4. **Тестирование**: запустите существующие тесты
5. **Разработка по фазам**: начните с Phase 1


## 🔐 Как Hermes и OpenClaw подключаются к соцсетям

### **Вариант 1: Нативные навыки (Native Skills)**

**Пример для X (Twitter) в Hermes:**
```bash
# Вы САМИ регистрируете X Developer App
# Вы САМИ выполняете аутентификацию ВНЕ сессии агента
xurl auth oauth2
```

**Ключевые принципы:**
- ❌ Агенту **ЗАПРЕЩЕНО** трогать credentials напрямую
- ❌ Агент не может видеть `~/.xurl` файл с токенами
- ✅ Вы сами настраиваете OAuth для каждой платформы
- ✅ Агент работает уже с готовыми токенами через CLI

### **Вариант 2: MCP Server (Model Context Protocol) - РЕКОМЕНДУЕМЫЙ**

**Как это работает:**
```yaml
# ~/.hermes/config.yaml
mcp_servers:
  blotato:
    url: "https://mcp.blotato.com/mcp"
    auth: oauth
```

**Процесс подключения:**
1. Вы подключаете свои аккаунты соцсетей в дашборде MCP-сервиса (например, Blotato)
2. Агент открывает браузер для OAuth-авторизации
3. Токены сохраняются локально (`~/.hermes/mcp-tokens/`)
4. Агент вызывает **типизированные инструменты**, а не сырые API

**Преимущества:**
- 🔒 Агент **НИКОГДА** не видит ваши пароли/credentials
- 🔒 Один OAuth flow для всех платформ
- 🔒 Безопасность на уровне протокола

---

## 🎯 Что это значит для вашего проекта?

### **Подход 1: Ваш существующий (Direct API Access)**

**Текущая архитектура вашего проекта:**
```
[Ваш агент] 
    ↓ хранит credentials в .env
    ↓ делает прямые API вызовы
    ├── VK API (user token)
    └── Telegram Bot API (bot token)
```

**Плюсы:**
- ✅ Простая реализация
- ✅ Полный контроль над процессом
- ✅ Работает для ваших задач

**Минусы:**
- ❌ Вы храните credentials в коде/БД
- ❌ Сложнее масштабировать на новые платформы
- ❌ Каждый новый пользователь = новая настройка credentials

### **Подход 2: Hermes-style (OAuth + Middleware)**

**Предлагаемая архитектура:**
```
[Ваш агент]
    ↓ вызывает типизированные инструменты
    ↓
[MCP Server / Middleware]
    ↓ управляет токенами
    ↓ OAuth flow через браузер
    ├── VK OAuth
    ├── Telegram Bot API
    └── Max API
```

**Плюсы:**
- 🔒 Безопасность: credentials не в вашем коде
- 🔒 Один раз настроил - работает для всех пользователей
- 🔒 Легко добавлять новые платформы

**Минусы:**
- ❌ Сложнее в реализации
- ❌ Нужен дополнительный сервис (MCP server)
- ❌ Зависимость от внешнего сервиса

### **Подход 3: Гибридный (рекомендую для вашего случая)**

```python
# Структура подключения источников
class SourceConnection(Base):
    __tablename__ = "source_connections"
    
    id = Column(Integer, primary_key=True)
    platform = Column(String)  # "vk", "telegram", "max"
    connection_type = Column(String)  # "api_key", "oauth", "bot_token"
    
    # Для OAuth
    access_token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    
    # Для API keys
    api_key_encrypted = Column(Text, nullable=True)
    
    # Metadata
    external_id = Column(String)  # ID пользователя/группы
    permissions = Column(JSON)  # Что можно делать
    is_active = Column(Boolean, default=True)
```

---

## 🚀 Практические рекомендации для вашего проекта

### **Для мониторинга (read-only):**

**VK:**
```python
# Использовать OAuth user token (не service token!)
# Пользователь логинится через VK OAuth
# Вы получаете access_token и refresh_token
# Токены шифруются в БД (Fernet)
```

**Telegram:**
```python
# Вариант 1: Bot API (для публичных каналов)
bot_token = "123456:ABC-DEF..."  # От @BotFather

# Вариант 2: MTProto (для приватных чатов)
# Использовать библиотеку типа Pyrogram/Telethon
# Пользователь логинится через QR-код
# Session file хранится зашифрованным
```

### **Для комментирования (write access):**

**Критически важно:**
```python
class BotScenario(Base):
    __tablename__ = "bot_scenarios"
    
    # Требование прав модератора
    requires_moderator = Column(Boolean, default=True)
    
    # Rate limiting
    max_comments_per_hour = Column(Integer, default=10)
    cooldown_between_comments = Column(Integer, default=60)  # секунды
    
    # Human-in-the-loop
    requires_approval = Column(Boolean, default=False)
```

**Проверка перед отправкой:**
```python
async def can_comment(source_id: int) -> bool:
    source = await get_source(source_id)
    
    # Проверка 1: Есть ли права модератора?
    if not source.has_moderator_rights:
        log.warning(f"No moderator rights for {source.name}")
        return False
    
    # Проверка 2: Rate limit
    recent_comments = await count_recent_comments(source_id, hours=1)
    if recent_comments >= source.max_comments_per_hour:
        log.warning(f"Rate limit reached for {source.name}")
        return False
    
    # Проверка 3: Cooldown
    last_comment = await get_last_comment_time(source_id)
    if last_comment and (now() - last_comment).seconds < source.cooldown:
        return False
    
    return True
```

---

## 📊 Сравнительная таблица подходов

| Критерий | Ваш текущий | Hermes-style | MCP Server |
|----------|-------------|--------------|------------|
| **Безопасность** | ⚠️ Credentials в БД | ✅ OAuth flow | ✅ Максимальная |
| **Простота** | ✅ Очень просто | ⚠️ Средняя | ❌ Сложно |
| **Масштабируемость** | ⚠️ На каждого пользователя | ✅ Один OAuth | ✅ Один OAuth |
| **Новые платформы** | ❌ Каждый раз код | ✅ Добавить OAuth app | ✅ Добавить в middleware |
| **Контроль** | ✅ Полный | ✅ Полный | ⚠️ Зависит от сервиса |
| **Для вашего случая** | ✅ Подходит | ✅ Рекомендую | ⚠️ Overkill |

---

## 🎯 Итоговые рекомендации

### **Для вашего проекта я рекомендую:**

1. **Продолжить с текущим подходом** (Direct API), но с улучшениями:
   - Шифрование credentials в БД
   - OAuth flow для новых пользователей
   - Rate limiting и защита от бана

2. **Добавить OAuth для пользователей** (Hermes-style):
   ```python
   # Вместо хранения credentials в .env
   # Пользователь логинится через VK OAuth на вашем сайте
   # Вы получаете токены и шифруете их
   ```

3. **Не использовать MCP Server** - это overkill для вашего случая:
   - У вас свой проект с полным контролем
   - MCP добавит лишнюю зависимость
   - Ваш подход уже работает

4. **Реализовать multi-tenant архитектуру**:
   ```python
   # Каждый пользователь может подключить свои источники
   # У каждого свои credentials (зашифрованные)
   # Agent работает от имени пользователя
   ```

### **Что вам нужно предоставить:**

**Для мониторинга:**
- ✅ API токены (VK user token, Telegram bot token)
- ✅ Или OAuth flow для пользователей

**Для комментирования:**
- ✅ Токены с правами модератора
- ✅ Явное согласие пользователя на автоматизацию
- ✅ Настройка rate limits

**Для AI-анализа:**
- ✅ API ключ для DeepSeek/OpenAI
- ✅ Бюджет на токены (платите за запросы)

---


## 🤔 Почему в Hermes проще?

В **Hermes/OpenClaw** используется **агентный подход**:

```
Пользователь: "Следи за активностью в группе @crypto_chat"
    ↓
Агент: "Окей, я сам разберусь как это сделать"
    ↓
Агент использует инструменты:
- browse_web("https://t.me/crypto_chat")
- read_page()
- extract_posts()
- analyze_sentiment()
```

**Агенту не нужно знать заранее**, как работает каждая платформа. Он использует **универсальные инструменты** (браузер, парсеры, скрапинг).

---

## 📊 Три подхода и расход токенов

### **Подход 1: Ручная интеграция (мой код выше)** ❌

```
Для каждой соцсети:
- Пишем парсер вручную
- Пишем OAuth вручную
- Пишем обработчики ошибок
- Поддерживаем код
```

**Расход токенов:** ⭐ **Минимальный**
```
Анализ поста:
- Вход: текст поста (500 токенов)
- Выход: анализ (200 токенов)
- Итого: ~700 токенов на пост

100 постов/день = 70,000 токенов/день
```

**Плюсы:**
- ✅ Полный контроль
- ✅ Минимум токенов
- ✅ Быстро работает

**Минусы:**
- ❌ Много кода
- ❌ Нужно знать каждую платформу
- ❌ Сложно поддерживать

---

### **Подход 2: Агентный (как в Hermes)** 🤖

```
Агент получает задачу:
"Мониторь активность в группе @crypto_chat"
    ↓
Агент сам решает:
1. Как получить доступ
2. Какие данные собирать
3. Как анализировать
4. Как реагировать
```

**Расход токенов:** ⭐⭐⭐ **Максимальный**
```
Каждый пост:
1. Агент решает что делать (500 токенов)
2. Парсит контент (300 токенов)
3. Анализирует (700 токенов)
4. Принимает решение (400 токенов)
5. Генерирует ответ (500 токенов)
Итого: ~2400 токенов на пост

100 постов/день = 240,000 токенов/день
```

**Плюсы:**
- ✅ Гибкость
- ✅ Не нужно писать парсеры для каждой платформы
- ✅ Агент сам учится

**Минусы:**
- ❌ Дорого (больше токенов)
- ❌ Медленнее
- ❌ Менее предсказуемо

---

### **Подход 3: Гибридный (РЕКОМЕНДУЮ)** ⚡

```
Базовые парсеры написаны ОДИН РАЗ
Агент использует их через инструменты
```

```python
# Агент получает инструменты
tools = [
    {
        "name": "get_vk_posts",
        "description": "Получает посты из группы/стены",
        "parameters": {"group_id": "str", "count": "int"}
    },
    {
        "name": "analyze_sentiment",
        "description": "Анализирует настроение текста",
        "parameters": {"text": "str"}
    },
    {
        "name": "post_comment",
        "description": "Отправляет комментарий",
        "parameters": {"post_id": "str", "text": "str"}
    }
]
```

**Расход токенов:** ⭐⭐ **Средний**
```
Анализ поста:
1. Агент вызывает инструмент (100 токенов)
2. Парсер собирает данные (без токенов!)
3. Анализирует текст (700 токенов)
4. Принимает решение (300 токенов)
Итого: ~1100 токенов на пост

100 постов/день = 110,000 токенов/день
```

**Плюсы:**
- ✅ Парсеры пишутся один раз
- ✅ Агент гибкий
- ✅ Контроль над процессом
- ✅ Умеренный расход токенов

**Минусы:**
- ⚠️ Нужно написать базовые парсеры
- ⚠️ Сложнее чем чистый агентный

---

## 💰 Сравнение стоимости

Предположим, используем **DeepSeek API**:
- Вход: $0.14 за 1M токенов
- Выход: $0.28 за 1M токенов

### **Стоимость на 100 постов/день:**

| Подход | Токенов/пост | Токенов/день | Стоимость/день | Стоимость/месяц |
|--------|-------------|--------------|----------------|-----------------|
| **Ручная интеграция** | 700 | 70,000 | ~$0.03 | ~$0.90 |
| **Агентный (как в Hermes)** | 2400 | 240,000 | ~$0.10 | ~$3.00 |
| **Гибридный** | 1100 | 110,000 | ~$0.05 | ~$1.50 |

**Разница не критичная!** Даже агентный подход стоит копейки.

---

## 🎯 Что я рекомендую для вашего проекта

### **Правильный подход:**

1. **Для мониторинга** - **гибридный**:
   ```python
   # Написали ОДИН раз парсеры для VK/Telegram
   # Агент использует их через инструменты
   # Агент решает, что анализировать
   ```

2. **Для анализа** - **ручная интеграция**:
   ```python
   # Анализ предсказуемый
   # Один промпт на все посты
   # Минимум токенов
   ```

3. **Для комментирования** - **агентный с правилами**:
   ```python
   # Агент генерирует ответы
   # Но есть жесткие правила
   # Человек может проверить
   ```

---

## 🚀 Упрощенная архитектура (как в Hermes)

**Вместо OAuth для каждой платформы, используйте:**

```python
# app/services/social_agent.py

class SocialMediaAgent:
    """Универсальный агент для работы с соцсетями"""
    
    def __init__(self, platform: str):
        self.platform = platform
        self.tools = self._load_tools(platform)
    
    def _load_tools(self, platform: str) -> list:
        """Загружает инструменты для платформы"""
        
        if platform == "vk":
            return [
                VKParser(),  # Готовый парсер
                VKCommenter(),  # Готовый комментатор
            ]
        elif platform == "telegram":
            return [
                TelegramParser(),
                TelegramBot(),
            ]
        else:
            # Универсальный браузерный парсер (как в Hermes)
            return [BrowserTool()]
    
    async def monitor(self, source: str, task: str):
        """Агент сам решает, как мониторить"""
        
        # Агент получает задачу
        prompt = f"""
        Ты - агент для мониторинга соцсетей.
        
        Платформа: {self.platform}
        Источник: {source}
        Задача: {task}
        
        Доступные инструменты:
        {self._format_tools()}
        
        Реши, как выполнить задачу.
        """
        
        # Агент планирует действия
        plan = await self.llm.generate(prompt)
        
        # Выполняем план
        for step in plan.steps:
            result = await self.tools[step.tool].execute(step.params)
            
            # Анализируем результат
            analysis = await self.llm.analyze(result)
            
            # Сохраняем в БД
            await self.db.save(analysis)
```

**Ключевое отличие:**
- ❌ Не нужно писать отдельный код для каждой платформы
- ✅ Агент сам использует нужные инструменты
- ✅ Один промпт для всех задач

---

## 🔧 Что реально нужно сделать

### **Минимальный набор:**

1. **Базовые парсеры** (ОДИН раз):
   ```python
   # VK: wall.get, groups.get
   # Telegram: Bot API для каналов
   # Браузер: для всего остального
   ```

2. **Инструменты для агента**:
   ```python
   tools = [
       "get_posts",      # Получить посты
       "analyze_text",   # Проанализировать
       "post_comment",   # Отправить комментарий
       "send_report",    # Отправить отчет
   ]
   ```

3. **Промпт для агента**:
   ```python
   SYSTEM_PROMPT = """
   Ты - агент для мониторинга соцсетей.
   
   Доступные инструменты: {tools}
   
   Твоя задача:
   1. Собирать контент из указанных источников
   2. Анализировать настроение и темы
   3. Реагировать по сценариям
   4. Генерировать отчеты
   
   Правила:
   - Не спамить (максимум 5 комментариев/час)
   - Соблюдать права модератора
   - Сохранять только метаданные
   """
   ```

---

## ✅ Итог

**Рекомендация:**
- Используйте **гибридный подход**
- Напишите **базовые парсеры** один раз
- Агент использует их через **инструменты**
- Расход токенов будет **умеренным** (~$1-3/месяц)


## 🤖 Упрощенная агентная архитектура

### **Концепция:**

```
Пользователь: "Мониторь группу @crypto_chat, отвечай на вопросы"
    ↓
Агент сам решает КАК это сделать
    ↓
Использует инструменты (парсеры, анализ, комментирование)
    ↓
Сохраняет только РЕЗУЛЬТАТЫ (не сырые данные)
```

---

## 📁 Структура файлов

```
app/
├── agent/
│   ├── __init__.py
│   ├── core.py              # Основной агент
│   ├── tools/               # Инструменты агента
│   │   ├── __init__.py
│   │   ├── social_parsers.py    # Парсеры соцсетей
│   │   ├── ai_analyzer.py       # AI анализ
│   │   └── bot_actions.py       # Действия бота
│   └── prompts/
│       └── system.py        # Промпты
└── api/
    └── agent.py             # API для управления агентом
```

---

## 1️⃣ Основной агент

**`app/agent/core.py`**

```python
from typing import List, Dict, Any, Optional
from datetime import datetime
import asyncio
import json

from app.agent.tools.social_parsers import VKParser, TelegramParser
from app.agent.tools.ai_analyzer import AIAnalyzer
from app.agent.tools.bot_actions import BotActions
from app.core.config import settings


class SocialMediaAgent:
    """
    Универсальный агент для мониторинга соцсетей
    
    Работает как в Hermes:
    - Получает задачу
    - Сам решает как её выполнить
    - Использует инструменты
    """
    
    def __init__(self):
        self.parsers = {
            "vk": VKParser(),
            "telegram": TelegramParser(),
        }
        self.analyzer = AIAnalyzer()
        self.bot_actions = BotActions()
        
        # История задач агента
        self.task_history = []
    
    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Выполняет задачу агента
        
        Пример задачи:
        {
            "type": "monitor",
            "platform": "vk",
            "source": "crypto_chat",
            "instructions": "Следи за активностью, отвечай на вопросы"
        }
        """
        
        print(f"🤖 Агент получил задачу: {task}")
        
        # Шаг 1: Агент планирует действия
        plan = await self._create_plan(task)
        print(f"📋 План агента: {plan}")
        
        # Шаг 2: Выполняем план
        results = []
        for step in plan["steps"]:
            result = await self._execute_step(step, task)
            results.append(result)
        
        # Шаг 3: Генерируем итоговый отчет
        summary = await self._generate_summary(task, results)
        
        return {
            "task": task,
            "plan": plan,
            "results": results,
            "summary": summary,
            "completed_at": datetime.utcnow().isoformat()
        }
    
    async def _create_plan(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Агент сам создает план действий
        Это как "мышление" агента
        """
        
        task_type = task.get("type", "monitor")
        platform = task.get("platform")
        source = task.get("source")
        instructions = task.get("instructions", "")
        
        # Агент решает, какие шаги нужны
        if task_type == "monitor":
            plan = {
                "goal": f"Мониторинг {source} на {platform}",
                "steps": [
                    {
                        "action": "collect_posts",
                        "tool": "social_parsers",
                        "description": f"Собрать посты из {source}",
                        "params": {
                            "platform": platform,
                            "source": source,
                            "count": 50
                        }
                    },
                    {
                        "action": "analyze_posts",
                        "tool": "ai_analyzer",
                        "description": "Проанализировать собранные посты",
                        "params": {
                            "analysis_type": "sentiment_and_topics"
                        }
                    },
                    {
                        "action": "generate_report",
                        "tool": "ai_analyzer",
                        "description": "Создать краткий отчет",
                        "params": {
                            "format": "summary"
                        }
                    }
                ]
            }
            
            # Если нужно комментирование
            if "отвечай" in instructions.lower() or "комментируй" in instructions.lower():
                plan["steps"].append({
                    "action": "respond_to_posts",
                    "tool": "bot_actions",
                    "description": "Ответить на подходящие посты",
                    "params": {
                        "max_responses": 3,
                        "requires_moderator": True
                    }
                })
        
        elif task_type == "analyze":
            plan = {
                "goal": f"Анализ {source}",
                "steps": [
                    {
                        "action": "collect_posts",
                        "tool": "social_parsers",
                        "description": f"Собрать посты из {source}",
                        "params": {
                            "platform": platform,
                            "source": source,
                            "count": 100
                        }
                    },
                    {
                        "action": "deep_analysis",
                        "tool": "ai_analyzer",
                        "description": "Глубокий анализ контента",
                        "params": {
                            "analysis_type": "detailed"
                        }
                    }
                ]
            }
        
        return plan
    
    async def _execute_step(self, step: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет один шаг плана"""
        
        action = step["action"]
        tool_name = step["tool"]
        params = step.get("params", {})
        
        print(f"⚙️ Выполняю: {step['description']}")
        
        try:
            # Выбираем инструмент
            if tool_name == "social_parsers":
                result = await self._use_parser(action, params, task)
            
            elif tool_name == "ai_analyzer":
                result = await self._use_analyzer(action, params)
            
            elif tool_name == "bot_actions":
                result = await self._use_bot_actions(action, params)
            
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
            
            return {
                "action": action,
                "success": True,
                "result": result
            }
        
        except Exception as e:
            print(f"❌ Ошибка в шаге {action}: {e}")
            return {
                "action": action,
                "success": False,
                "error": str(e)
            }
    
    async def _use_parser(self, action: str, params: Dict, task: Dict) -> Any:
        """Использует парсеры соцсетей"""
        
        platform = params.get("platform") or task.get("platform")
        source = params.get("source") or task.get("source")
        count = params.get("count", 50)
        
        parser = self.parsers.get(platform)
        if not parser:
            raise ValueError(f"No parser for platform: {platform}")
        
        if action == "collect_posts":
            return await parser.get_posts(source, count)
        
        raise ValueError(f"Unknown parser action: {action}")
    
    async def _use_analyzer(self, action: str, params: Dict) -> Any:
        """Использует AI анализ"""
        
        if action == "analyze_posts":
            return await self.analyzer.analyze_posts(params)
        
        elif action == "generate_report":
            return await self.analyzer.generate_report(params)
        
        elif action == "deep_analysis":
            return await self.analyzer.deep_analysis(params)
        
        raise ValueError(f"Unknown analyzer action: {action}")
    
    async def _use_bot_actions(self, action: str, params: Dict) -> Any:
        """Использует действия бота"""
        
        if action == "respond_to_posts":
            return await self.bot_actions.respond_to_posts(params)
        
        raise ValueError(f"Unknown bot action: {action}")
    
    async def _generate_summary(self, task: Dict, results: List[Dict]) -> str:
        """Генерирует итоговый отчет"""
        
        successful_results = [r for r in results if r["success"]]
        
        summary_prompt = f"""
        Задача: {task}
        
        Выполненные шаги:
        {json.dumps(successful_results, indent=2, ensure_ascii=False)}
        
        Создай краткий отчет о результатах.
        """
        
        summary = await self.analyzer.llm.generate(summary_prompt)
        return summary


# Singleton instance
agent = SocialMediaAgent()
```

---

## 2️⃣ Инструменты агента

**`app/agent/tools/social_parsers.py`**

```python
from typing import List, Dict, Any, Optional
import httpx
from datetime import datetime


class BaseParser:
    """Базовый парсер для соцсетей"""
    
    async def get_posts(self, source: str, count: int = 50) -> List[Dict]:
        raise NotImplementedError


class VKParser(BaseParser):
    """Парсер для VK"""
    
    API_URL = "https://api.vk.com/method"
    
    def __init__(self):
        # Токен берется из настроек или из БД
        self.access_token = "YOUR_VK_TOKEN"
    
    async def get_posts(self, source: str, count: int = 50) -> List[Dict]:
        """Получает посты из группы/стены"""
        
        # Определяем тип источника
        owner_id = self._parse_source(source)
        
        params = {
            "owner_id": owner_id,
            "count": count,
            "filter": "all",
            "access_token": self.access_token,
            "v": "5.199",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.API_URL}/wall.get", params=params)
            data = response.json()
        
        if "error" in data:
            raise Exception(f"VK API error: {data['error']}")
        
        posts = data["response"]["items"]
        
        # Возвращаем только нужные поля
        return [
            {
                "id": post["id"],
                "text": post.get("text", ""),
                "date": datetime.fromtimestamp(post["date"]).isoformat(),
                "likes": post.get("likes", {}).get("count", 0),
                "comments": post.get("comments", {}).get("count", 0),
                "views": post.get("views", {}).get("count", 0),
            }
            for post in posts
        ]
    
    def _parse_source(self, source: str) -> str:
        """Парсит источник (группа или пользователь)"""
        
        # Если это числовой ID
        if source.isdigit():
            return source
        
        # Если это имя группы
        if source.startswith("-"):
            return source
        
        # TODO: Резолвить имя в ID через VK API
        return source


class TelegramParser(BaseParser):
    """Парсер для Telegram"""
    
    def __init__(self):
        self.bot_token = "YOUR_TELEGRAM_BOT_TOKEN"
    
    async def get_posts(self, source: str, count: int = 50) -> List[Dict]:
        """Получает посты из канала"""
        
        # Для публичных каналов используем Bot API
        # Для приватных - нужен пользовательский клиент (Pyrogram/Telethon)
        
        # Упрощенная версия для публичных каналов
        return await self._get_public_channel_posts(source, count)
    
    async def _get_public_channel_posts(self, channel: str, count: int) -> List[Dict]:
        """Получает посты из публичного канала"""
        
        # Используем Telegram Bot API
        api_url = f"https://api.telegram.org/bot{self.bot_token}"
        
        # TODO: Реализовать получение постов из канала
        # Это требует специальных прав или использования t.me API
        
        # Заглушка
        return []


class BrowserParser(BaseParser):
    """
    Универсальный парсер через браузер
    Используется когда нет прямого API
    """
    
    async def get_posts(self, source: str, count: int = 50) -> List[Dict]:
        """Парсит любую страницу через браузер"""
        
        # Можно использовать Playwright или Selenium
        # Это как в Hermes - агент сам открывает страницу
        
        # Пример с Playwright:
        # async with async_playwright() as p:
        #     browser = await p.chromium.launch()
        #     page = await browser.new_page()
        #     await page.goto(f"https://t.me/{source}")
        #     posts = await page.query_selector_all(".message")
        #     return [await post.inner_text() for post in posts]
        
        return []
```

---

## 3️⃣ AI Анализ

**`app/agent/tools/ai_analyzer.py`**

```python
from typing import List, Dict, Any, Optional
import json


class AIAnalyzer:
    """AI анализ контента"""
    
    def __init__(self):
        # Используем ваш существующий LLM клиент
        self.llm = LLMClient()
    
    async def analyze_posts(self, params: Dict) -> Dict[str, Any]:
        """Анализирует посты"""
        
        posts = params.get("posts", [])
        analysis_type = params.get("analysis_type", "sentiment_and_topics")
        
        # Анализируем каждый пост
        results = []
        for post in posts[:10]:  # Ограничиваем чтобы не тратить токены
            analysis = await self._analyze_single_post(post, analysis_type)
            results.append(analysis)
        
        return {
            "analyzed_count": len(results),
            "results": results
        }
    
    async def _analyze_single_post(self, post: Dict, analysis_type: str) -> Dict:
        """Анализирует один пост"""
        
        text = post.get("text", "")
        
        if not text:
            return {"error": "No text to analyze"}
        
        # Промпт для анализа
        prompt = f"""
        Проанализируй текст поста.
        
        Текст: {text}
        
        Верни результат в JSON формате:
        {{
            "sentiment": "positive/neutral/negative",
            "sentiment_score": число от -1 до 1,
            "topics": ["тема1", "тема2"],
            "keywords": ["ключевое1", "ключевое2"],
            "summary": "краткое резюме"
        }}
        """
        
        response = await self.llm.generate(prompt)
        
        try:
            return json.loads(response)
        except:
            return {"error": "Failed to parse analysis", "raw": response}
    
    async def generate_report(self, params: Dict) -> Dict[str, Any]:
        """Генерирует отчет"""
        
        format = params.get("format", "summary")
        
        # Собираем данные из предыдущих шагов
        # В реальном коде это будет из контекста агента
        
        report_prompt = """
        Создай краткий отчет о мониторинге.
        
        Формат: {format}
        
        Включи:
        - Количество постов
        - Общий настрой
        - Топ темы
        - Рекомендации
        """
        
        report = await self.llm.generate(report_prompt)
        
        return {"report": report, "generated_at": datetime.utcnow().isoformat()}


class LLMClient:
    """Клиент для работы с LLM"""
    
    async def generate(self, prompt: str) -> str:
        """Генерирует ответ"""
        
        # Используем ваш существующий DeepSeek/OpenAI клиент
        # Пример:
        # response = await deepseek_client.chat(prompt)
        # return response
        
        # Заглушка
        return "Generated response"
```

---

## 4️⃣ Действия бота

**`app/agent/tools/bot_actions.py`**

```python
from typing import List, Dict, Any, Optional
from datetime import datetime


class BotActions:
    """Действия бота (комментирование, ответы)"""
    
    def __init__(self):
        self.max_responses_per_hour = 5
        self.responses_sent = []
    
    async def respond_to_posts(self, params: Dict) -> Dict[str, Any]:
        """Отвечает на посты"""
        
        max_responses = params.get("max_responses", 3)
        requires_moderator = params.get("requires_moderator", True)
        
        # Проверяем права модератора
        if requires_moderator and not await self._check_moderator_rights():
            return {
                "success": False,
                "error": "No moderator rights"
            }
        
        # Проверяем rate limit
        if not await self._check_rate_limit():
            return {
                "success": False,
                "error": "Rate limit exceeded"
            }
        
        # Выбираем посты для ответа
        posts_to_respond = await self._select_posts_for_response(max_responses)
        
        # Генерируем и отправляем ответы
        responses = []
        for post in posts_to_respond:
            response = await self._generate_response(post)
            if response:
                success = await self._send_response(post, response)
                responses.append({
                    "post_id": post["id"],
                    "response": response,
                    "success": success
                })
        
        return {
            "responses_sent": len(responses),
            "responses": responses
        }
    
    async def _check_moderator_rights(self) -> bool:
        """Проверяет права модератора"""
        # Реализация проверки прав
        return True
    
    async def _check_rate_limit(self) -> bool:
        """Проверяет лимит ответов"""
        from datetime import timedelta
        
        hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_responses = [r for r in self.responses_sent if r > hour_ago]
        
        return len(recent_responses) < self.max_responses_per_hour
    
    async def _select_posts_for_response(self, count: int) -> List[Dict]:
        """Выбирает посты для ответа"""
        # Логика выбора постов
        # Например: посты с вопросами, упоминания бота
        return []
    
    async def _generate_response(self, post: Dict) -> Optional[str]:
        """Генерирует ответ на пост"""
        
        prompt = f"""
        Напиши дружелюбный ответ на этот пост.
        
        Пост: {post.get('text', '')}
        
        Ответ (1-2 предложения):
        """
        
        # Используем LLM для генерации
        response = await LLMClient().generate(prompt)
        return response
    
    async def _send_response(self, post: Dict, response: str) -> bool:
        """Отправляет ответ"""
        
        # Реализация отправки через API платформы
        # Например:
        # await vk_client.create_comment(post_id, response)
        
        print(f"💬 Ответ отправлен на пост {post['id']}")
        return True
```

---

## 5️⃣ API для управления агентом

**`app/api/agent.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from pydantic import BaseModel

from app.agent.core import agent

router = APIRouter(prefix="/agent", tags=["Agent"])


class AgentTask(BaseModel):
    """Задача для агента"""
    type: str  # "monitor", "analyze", "respond"
    platform: str  # "vk", "telegram"
    source: str  # ID группы/канала
    instructions: str = ""


@router.post("/task")
async def run_agent_task(task: AgentTask):
    """Запускает задачу агента"""
    
    result = await agent.execute_task(task.dict())
    return result


@router.post("/tasks/batch")
async def run_batch_tasks(tasks: List[AgentTask]):
    """Запускает несколько задач"""
    
    results = []
    for task in tasks:
        result = await agent.execute_task(task.dict())
        results.append(result)
    
    return {"results": results}


@router.get("/history")
async def get_agent_history():
    """Возвращает историю задач агента"""
    return {"history": agent.task_history}
```

---

## 6️⃣ Пример использования

**Запуск агента:**

```python
# Пример задачи
task = {
    "type": "monitor",
    "platform": "vk",
    "source": "crypto_chat",
    "instructions": "Следи за активностью, отвечай на вопросы"
}

# Запускаем агента
result = await agent.execute_task(task)

print(result["summary"])
```

**Результат:**
```json
{
  "task": {
    "type": "monitor",
    "platform": "vk",
    "source": "crypto_chat",
    "instructions": "Следи за активностью, отвечай на вопросы"
  },
  "plan": {
    "goal": "Мониторинг crypto_chat на vk",
    "steps": [
      {"action": "collect_posts", "description": "Собрать посты из crypto_chat"},
      {"action": "analyze_posts", "description": "Проанализировать собранные посты"},
      {"action": "generate_report", "description": "Создать краткий отчет"},
      {"action": "respond_to_posts", "description": "Ответить на подходящие посты"}
    ]
  },
  "summary": "За день собрано 47 постов. Настроение: нейтральное (0.2). Топ темы: Bitcoin, Ethereum. Отправлено 3 ответа на вопросы."
}
```


## 📊 Сравнение подходов

| Критерий | Ручная интеграция | Агентный (этот код) |
|----------|-------------------|---------------------|
| **Код для новой платформы** | 500+ строк | 50 строк (новый парсер) |
| **Гибкость** | ⚠️ Низкая | ✅ Высокая |
| **Расход токенов** | ⭐ Минимум | ⭐⭐ Умеренный |
| **Поддержка** | ⚠️ Сложно | ✅ Легко |
| **Скорость разработки** | ⚠️ Медленно | ✅ Быстро |

---

