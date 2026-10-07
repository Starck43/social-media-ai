# Обзор конкурентов и итоговая матрица заимствований

## Оговорка по ссылкам

Точные URL репозиториев лежат в вашем исследовательском документе `analysis_and_best_experiences.md` (там таблица аналогов без ссылок). Чтобы не фабриковать адреса, я цитирую **имена из вашего исследования**, а уверенные публичные URL даю только для общеизвестных проектов: Mem0 — github.com/mem0ai/mem0, Cognee — github.com/topoteretes/cognee, Langfuse — github.com/langfuse/langfuse, pgvector — github.com/pgvector/pgvector, Telethon — github.com/LonamiWebs/Telethon.

---

## Кластер 1: LLM-надёжность и стоимость (ops)

**SmIA** — валидация ответов LLM Pydantic-моделями + Langfuse-трейсинг. У вас схема ответа уже формализована (`JSONSchemaBuilder` + контрактный тест), но парсинг — regex `extract_json()`. Заимствуем не «ещё одну обёртку», а принцип: **схема = код, парсинг = валидатор**. Побочный выигрыш: ваш relevance-фильтр (`is_meaningful`/`confidence`) станет надёжным, а не «если regex поймал».

**rss-bot (AUTOMODEL)** — фоновый проб моделей с авто-переключением при деградации. У вас есть `chat_with_fallback` (реактивный) и админский «Test connection» (ручной). Заимствуем **probe-job в реестре `HANDLERS`** (без миграции) + авто-уведомление в `notifications` при деградации.

**ai-news-digest** — двухмодельный пайплайн (дешёвый селектор + качественный писатель). У вас это **уже заложено, но не работает**: поле `AgentScenario.llm_strategy` (cost_efficient/quality/multimodal) существует, но авто-выбор везде описан как «первая активная модель». Заимствуем не новую сущность, а **enforcement существующей**: `resolve_default_model()` должен уважать `llm_strategy` и `is_default`.

**OpenCrow / SmIA (cost per call)** — стоимость видна до токена. У вас данные для атрибуции стоимости **уже собраны**, но не порезаны: `jobs.llm_cost` имеет `agent_task_id`, `digest_runs.llm_cost` — задачу, `agent_messages.cost` — сессию. Заимствуем идею дашборда, но реализуем её как **отчёт «стоимость по задачам/сценариям»** — новых таблиц не нужно, только агрегация.

**Telo-watch-tower / Syne / Ratatoskr** — семантический дедуп через embeddings. У вас точный hash-дедуп (отличный первый контур). Заимствуем второй контур: cosine-порог против недавних embedding при collect. Ключевая поправка из нашей прошлой работы: **один embedding-сервис на три потребителя** (дедуп, chain resolver, semantic memory), иначе три тарификации.

## Кластер 2: Память и обучение

**Mem0** — факты с confidence, авто-повышение при повторных подтверждениях, авто-удаление противоречий. У вас есть `confidence` + `provenance` + `reflect`. Не хватает **статистики использования**: `access_count`, `last_accessed`. Заимствуем не новый job, а **данные для существующего `reflect`** — decay станет data-driven вместо чисто LLM-решения.

**Lethe** — кривая забывания / TTL. Та же история: TTL считается из `last_accessed` внутри `reflect`. Отдельная сущность не нужна.

**Syne / Cognee** — графы знаний. **Отклоняем**: ваши `entities[]` (person/brand/org) + `topic_chain_id` уже дают связь «сущность ↔ тема» без новой инфраструктуры. Граф оверкилл на ваших объёмах.

**Glean / Hermes Agent** — слой скиллов/плагинов. **Отклоняем как отдельный реестр**: ваш реестр инструментов и есть skill-система, а сценарий после рефакторинга — чистая методология. Добавление `skill_steps` в сценарий разрушило бы границу, которую мы только что вычистили.

## Кластер 3: Дайджесты и проактивность

**ai-news-digest / rss-bot** — двухстадийная сводка. У вас гибрид **сильнее**, чем у них: алгоритмический бриф (SQL/JSONB) вместо дешёвой LLM-стадии селекции. Ничего не заимствуем, фиксируем как преимущество.

**BuzzAgent / Nuggets** — проактивные алерты при аномалиях, а не только реактивные отчёты. У вас есть триггеры (правила на пост) и таблица `notifications`, но **нет статистических аномалий по агрегату**. Заимствуем: job сравнивает текущее окно с базовой линией (z-score тональности, всплеск токсичности) и шлёт `notification_type=alert`. Ваш же дайджест-блок «Динамика» уже считает дельты — переиспользуем его пороги («no change» < 0.02), чтобы алерты не кричали волком.

**claude-digest / twidgest-bot** — тарифы + Telegram Stars. Планы (`PLAN_LIMITS`) есть, оплаты нет. Заимствуем позже, это продуктовое решение, не технический долг.

**IntelFlow / BuzzAgent / akyn-bot** — билингвальность. У вас `tenants.agent_style.language` **уже существует**, но не пронесён в нарратив дайджеста и render. Заимствование копеечное: передать language в `_summarize()` и `render.py`.

**twidgest-bot** — онбординг «шаблоны + AI-подсказка» (`/createchannel ai <topic>`). У вас половина есть: `scenario_templates` + `scenario_suggest_prompt`. Не хватает **suggestion источников**: `source_suggest(topic)` — LLM предлагает кандидаты публичных групп/каналов, оператор подтверждает. Это закрывает самый ручной шаг онбординга.

## Кластер 4: Безопасность и воспроизводимость

**Iva / Agent Life Space** — санитизация prompt-injection. Ваш вектор шире, чем у них: в промпт анализа подставляется `{text}` — **недоверенный текст третьих лиц** из соцсетей, а не только реплики пользователя. Заимствуем санитайзер + instruction-defense framing перед подстановкой. Blast-radius ограничен (структурированный вывод + confirmation на записи), но нарратив дайджеста отравляем.

**Kit** — error-learning post-hook: провальные вызовы становятся уроками. У вас `/bad` + `reflect → prompt_advice`, но применение ручное и точки применения нет. Заимствуем авто-захват провальных tool_calls в Correction-строки памяти; применение — по-прежнему через подтверждение.

**socmon** — снимок конфига/промпта на каждый прогон. Заимствуем `prompt_hash` + scope/payload-снапшот в `ai_analytics`. Это не только аудит «почему анализ изменился», но и **ключ к автоматизации ре-анализа**: агент видит строки со старым хэшем и предлагает точечный ре-анализ затронутых дат/источников вместо грубого `force_reanalyze` на всё окно.

---

## Что у вас избыточно / вручную — и что отдать агенту или автоматике

| # | Ручная операция сейчас | Чем заменить | Источник идеи |
|---|---|---|---|
| 1 | Применение `prompt_advice` из reflect — вручную, точки применения нет | Tool `prompt_advice_apply`: дифф `base_prompt` + подтверждение в чате | Kit + ваш reflect |
| 2 | «Test connection» моделей — только кнопка в админке | Probe-job + авто-алерт; tool `llm_model_test` | rss-bot AUTOMODEL |
| 3 | Поиск источников при онбординге (ID/URL вручную) | Tool `source_suggest(topic)` с подтверждением | twidgest-bot |
| 4 | Магические пороги триггеров (0.3 и т.п.) | Tool `trigger_calibrate`: перцентиль распределения тональности за N дней → предложенный порог | BuzzAgent (аномалии) |
| 5 | Выбор группировки дайджеста/дашборда вручную | Агент рекомендует ось по плотности цепочек (`analytics_chains`) | наша архитектура цепочек |
| 6 | Гигиена памяти — частично LLM-решение | `access_count`/`last_accessed` → decay в `reflect` по статистике | Mem0, Lethe |
| 7 | Ре-анализ после смены промпта — грубый `force_reanalyze` на всё окно | Точечный ре-анализ по `prompt_hash` (только затронутые строки) | socmon |
| 8 | Контроль стоимости — смотреть на кап постфактум | Проактивный `notification` при 80% дневного капа | BuzzAgent, Nuggets |
| 9 | Чёрные/белые списки — ручное ведение | Агент предлагает пополнение из исходов модерации (`bot_actions.failed`, `/bad`) | Kit |
| 10 | Отчёт «сколько стоит каждая задача» — собирается руками из админки | Агрегация `jobs.llm_cost`/`digest_runs.llm_cost` по `agent_task_id` в дашборд | OpenCrow, SmIA |

**Избыточно в коде (упростить, а не автоматизировать):**
- regex `extract_json()` → валидируемый структурированный вывод (хрупкость + retry-циклы);
- детекция «промпт уже содержит JSON» по словам «формат»/«json» → явный флаг или парсер;
- пересечение двух force-флагов в форме задачи (флагировали ранее);
- декоративный `is_default` → единый `resolve_default_model()`;
- из `ANALYTICS_AGGREGATION_SYSTEM.md` раздел «Будущие улучшения» (Redis-кэш, WebSocket, PDF-экспорт, materialized views) — **вычеркнуть**: противоречит философии одного VPS и вашим объёмам.

---

## Итоговая матрица заимствований

| # | Практика | У кого подсмотрено | Куда ложится у вас | Приоритет | Труд |
|---|---|---|---|---|---|
| 1 | Structured outputs вместо regex | SmIA, Ratatoskr, IntelFlow | `json_schema_builder` + Pydantic-модели ответов | P1 | S |
| 2 | Injection-санитайзер перед `{text}` | Iva, Agent Life Space | `prompts.py::_prepare_variables` | P1 | S |
| 3 | Аудит-снапшот (prompt_hash, scope) | socmon | `ai_analytics.response_payload.request` | P1 | S |
| 4 | Enforcement `llm_strategy` + `is_default` | ai-news-digest, rss-bot | `resolve_default_model()` | P1 | S |
| 5 | Probe-job здоровья моделей + авто-алерт | rss-bot (AUTOMODEL), Telo-watch-tower | `HANDLERS` registry + `notifications` | P2 | M |
| 6 | Embedding-сервис: дедуп + chains + memory | Telo-watch-tower, Syne, Ratatoskr, Mem0 | `app/services/ai/embeddings.py` (pgvector) | P2 | L |
| 7 | Data-driven decay памяти | Mem0, Lethe | колонки + `reflect` | P2 | S |
| 8 | Error-learning: провалы → Correction | Kit | `agent_memory` + подтверждение | P2 | M |
| 9 | Статистические аномалии → alert | BuzzAgent, Nuggets | новый job + пороги из digest-динамики | P2 | M |
| 10 | Source suggestion при онбординге | twidgest-bot | tool `source_suggest` | P3 | M |
| 11 | Билингвальность дайджеста | IntelFlow, BuzzAgent, akyn-bot | `agent_style.language` → `_summarize`/`render` | P3 | S |
| 12 | Стоимость по задачам/сценариям | OpenCrow, SmIA | агрегация существующих `llm_cost` | P3 | S |
| 13 | Telegram Stars billing | claude-digest, twidgest-bot | планы уже есть | P4 | L |
| 14 | Knowledge graph | Syne, Cognee | **отклонено** (entities + chains достаточно) | — | — |
| 15 | Skill-реестр поверх сценариев | Glean, Hermes Agent | **отклонено** (tools = скиллы) | — | — |
| 16 | Redis-кэш / WebSocket / PDF | ANALYTICS-док «будущее» | **вычеркнуть** (философия одного VPS) | — | — |

**Читаемость матрицы:** P1 — фундамент надёжности (делает всё остальное безопаснее и дешевле); P2 — интеллект и проактивность; P3 — продуктовый лоск; P4 — монетизация; прочерк — сознательные отказы.

Главный вывод обзора: у вас **меньше пробелов, чем кажется из сравнительной таблицы** — шесть «заимствований» на деле являются enforcement уже существующих полей (`llm_strategy`, `is_default`, `agent_style.language`, `prompt_advice`, `notifications`, cost-колонки). Реально нового кода требуют только P1-четвёрка и embedding-платформа; остальное — доводка того, что уже построено.
