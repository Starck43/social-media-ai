# Agent runtime

Персональный AI-агент в личном чате мессенджера (Telegram/MAX): инструменты,
диалог с памятью, подтверждения опасных операций.

## Как это работает

```
messenger message
  -> app/channels/listener.py        poll (long polling), per-channel
  -> app/agent/runtime.py            handle_inbound()
      1. resolve_inbound: chat -> tenant (bound chat / invite code / owner)
      2. tenant_scope(tenant_id): все менеджеры фильтруют по workspace
      3. session = agent_sessions.get_or_create(channel, chat_id)
      4. цикл: LLM.chat(history + tools) -> выполнить tool_calls -> повторить
      5. ответ отправляется обратно в тот же чат
```

## Модули

| Модуль | Роль |
|---|---|
| `app/agent/runtime.py` | Шагающий цикл агента: сессия, лимиты, подтверждения, loop, сборка системного промпта (`build_system_prompt`) |
| `app/agent/prompts.py` | Системный промпт + текст `/help`; `render_style_block` (tenants.agent_style) |
| `app/agent/learning.py` | `run_learn` (факты из чата, watermark), `run_reflect` (гигиена памяти) |
| `app/agent/session.py` | Загрузка/нормализация истории для LLM |
| `app/agent/tools.py` | Реестр инструментов, OpenAI-схемы, диспетчеризация |
| `app/agent/toolset/` | Реализации тулов: `system`, `collect`, `sources`, `tasks`, `reports`, `actions`, `scenarios` |
| `app/models/agent_session.py` | Одна строка на (channel, chat_id); volatile `state` |
| `app/models/agent_message.py` | Транскрипт диалога (user/assistant/tool) + токены/стоимость |
| `app/models/agent_feedback.py` | Оценки `/good`,`/bad` (vote, note, сообщение-основание) |
| `app/models/agent_memory.py` | Факты в постоянной памяти: (tenant, scope, key) -> value + provenance |

## Инструменты

Все инструменты зарегистрированы в `TOOL_REGISTRY` в `app/agent/tools.py`
(импорт `toolset` срабатывает по побочному эффекту). Схемы уходят в LLM как
OpenAI function calling.

Запись/отправление (`confirm=True`): `task_add/remove/pause`, `source_add/disable`,
`digest_send_now`, `action_send`, `scenario_create/update/clone/delete`. Их модель
не выполняет сама — runtime кладёт вызов в
`session.state['pending_confirmation']` и ждёт явного «да»/«нет» от владельца.

## Команды чата

- `/help` — список возможностей
- `/stop` — очистить историю (session остаётся)
- `/good` — отметить последний ответ как удачный (в `agent_feedback`)
- `/bad <заметка>` — жалоба на последний ответ; заметки копятся для рефлексии
- `/memory clear` — стереть выученные факты (только владелец; watermark `learn` остаётся)

## Память и обучение

`memory_set`/`memory_get` — durable KV, скоуп на workspace
(см. `agent_memory_manager`). Теперь память **подмешивается в системный промпт**
автоматически: `build_system_prompt()` (`app/agent/runtime.py`) добавляет
соглашение о стиле (`tenants.agent_style`) и top-N фактов по `confidence`
(`agent_memory.snapshot()`, scope `meta` исключён).

Каждый факт несёт provenance: `source` (`manual`|`learn`|`reflect`),
`confidence` (0.1–1.0), `evidence_message_id` (сообщение-основание, FK SET NULL).

Обучение — два фоновых jobs (см. `docs/AGENT_TASKS.md`):

- `learn` (`app/agent/learning.py::run_learn`) — вытаскивает
  устойчивые факты/предпочтения из новых реплик чата. Watermark в
  `agent_memory(scope=meta,key=learn_msg_wm)`: LLM дёргается только когда
  накопилось `min_messages` (default 8) новых user-реплик — иначе дешёвый skip.
- `reflect` (`run_reflect`) — еженедельная гигиена: сливает
  дубли, чистит устаревшее, понижает `confidence` спорного; из `/bad`-заметок
  готовит **предложение** правки промптов (`prompt_advice`) — применяется
  вручную, автоматической точки правки нет.

Дефолтные cron для обоих заданы в `app/tasks/bootstrap.py`
(`DEFAULT_TASKS`) — там и смотреть актуальные значения.

Промпты агента живут в `AgentScenario`:
`base_prompt` (основная инструкция) + `media_overrides` (JSON: `image`/`video`/`audio` для мультимедиа) + `summary_prompt` (сводка).
Правка — через чат (`scenario_*`), `PUT /api/v1/ai/scenarios/{id}`, веб-мастер
(`/app/scenarios/new`) или админку (`AgentScenarioAdmin`); изменение
подхватывается со следующего запуска задачи (сценарий читается на каждый
run). Автоматическая эволюция промптов **не происходит**: `prompt_advice` из
`reflect` владелец применяет сам.

## Сценарии в чате

Инструменты в `app/agent/toolset/scenarios.py` (см. `docs/CHAT_BOT_SCENARIOS.md`):

| Tool | Назначение | Confirm |
|---|---|---|
| `scenario_list` / `scenario_get` | список и полная карточка сценария | нет |
| `scenario_templates` | готовые пресеты (`app/services/ai/scenario_templates.py`) | нет |
| `scenario_suggest_prompt` | описание задачи → `base_prompt` через LLM | нет |
| `scenario_validate_prompt` | неизвестные переменные в промпте | нет |
| `scenario_create` | из пресета или с нуля | да |
| `scenario_update` | частичное обновление, `scope` сливается по типам | да |
| `scenario_clone` | копия под новым именем (без флага default) | да |
| `scenario_delete` | удаление; возвращает число задач, оставшихся без сценария | да |

Подтверждение — общее для всех write-tools: runtime кладёт вызов в
`session.state['pending_confirmation']` и ждёт «да».

Предпочтения сценариев (`app/services/ai/scenario_prefs.py`) пишутся в
`agent_memory(scope=scenario_prefs)`: режим группировки и список брендов/конкурентов.
Читаются как дефолты в `scenario_create` и в первом шаге веб-мастера — пустые
поля заполняются, явно выбранные — нет.

Порядок работы с новым сценарием описан в системном промпте
(`SCENARIO_SECTION` в `app/agent/prompts.py`, добавляется в
`build_system_prompt()` независимо от `AGENT_SYSTEM_PROMPT`): цель → шаблон →
поля → промпт → валидация → превью → подтверждение.

Подробности: `docs/CHAT_BOT_SCENARIOS.md` (архитектура, переменные, валидация).
Исторический контекст: `docs/PROMPT_AND_SCOPE_EXPLAINED.md`.

## Лимиты и стоимость

| Параметр | Назначение |
|---|---|
| `AGENT_DAILY_COST_LIMIT` | USD-потолок за сегодня (UTC-день): агент + дайджесты + обучение (`learn`/`reflect`); проверка до и во время цикла |
| `tenant.daily_cost_limit` | Персональный лимит рабочего пространства (перекрывает глобальный) |
| `AGENT_MAX_ITERATIONS` | Макс. раундов tool-calls на одно сообщение |
| `AGENT_HISTORY_LIMIT` | Сколько сообщений истории уходит в LLM |
| `AGENT_MAX_TOKENS` / `AGENT_TEMPERATURE` | Параметры `chat()` |
| `AGENT_MODEL` | Явная модель; пусто = авто-выбор первой активной |

Метрика расхода — `tenancy.resolver.daily_cost_today()`: сумма
`AgentMessage.cost` (чат, пишется из `usage["cost"]`), `DigestRun.llm_cost`
(LLM-сводка дайджеста) и `Job.llm_cost` (`learn`/`reflect`, пишется из
`usage["cost"]` ответа `chat_with_fallback` через результат job'а).
Тарифы — из `llm_models` (`llm_client.price_usage_usd`),
столько же записывает `analyze()` в top-level `usage` дайджеста. Дайджест и
`learn`/`reflect` при исчерпанном лимите пропускают LLM-вызов (дайджест при
этом публикуется без LLM-сводки). См. `docs/AGENT_TASKS.md` для
общей картины очереди.

## Tenancy

Каждый чат принадлежит ровно одному рабочему пространству
(`tenant_channels`, резолвится в `app/services/tenancy/resolver.py`).
Вся работа агента выполняется внутри `tenant_scope(tenant_id)` — система
лишний раз фильтрует и пишет только своё, даже если инструмент забудет
указать tenant_id. Подробности: `docs/TENANCY.md`.

## Тесты

`tests/test_agent.py`: confirmation round-trip, отмена, plain answer, лимит
стоимости, незнакомцы игнорируются, listener шлёт ответ в тот же чат, `/help`,
проброс лимитов в `chat()`. Модель и HTTP не дёргаются — `_chat` стабится.
