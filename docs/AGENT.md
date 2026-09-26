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

Запись/отправка (`confirm=True`): `task_add/remove/pause`, `source_add/disable`,
`digest_send_now`, `action_send`, `scenario_assign`. Их модель не
выполняет сама — runtime кладёт вызов в `session.state['pending_confirmation']`
и ждёт явного «да»/«нет» от владельца.

Чтение/эфемерные: `collect_now`, `sources_list`, `scenario_list`,
`report_period`, `system_status`, `memory_get/set`, `task_list`, `actions_log`.

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

Дефолтные cron для обоих заданы в `app/scheduler/bootstrap.py`
(`DEFAULT_TASKS`) — там и смотреть актуальные значения.

Промпты агента живут в `AgentScenario` (`text_prompt` и модальные варианты
`image/video/audio/unified_summary_prompt`). Правка — через
`PUT /api/v1/ai/scenarios/{id}` или админку (`AgentScenarioAdmin`);
изменение подхватывается со следующего запуска задачи (сценарий читается
на каждый run). Автоматическая эволюция промптов **не происходит**:
`prompt_advice` из `reflect` владелец применяет сам.

## Лимиты и стоимость

| Параметр | Назначение |
|---|---|
| `AGENT_DAILY_COST_LIMIT` | USD-потолок агента за сегодня (UTC-день); проверка до и во время цикла |
| `tenant.daily_cost_limit` | Персональный лимит рабочего пространства (перекрывает глобальный) |
| `AGENT_MAX_ITERATIONS` | Макс. раундов tool-calls на одно сообщение |
| `AGENT_HISTORY_LIMIT` | Сколько сообщений истории уходит в LLM |
| `AGENT_MAX_TOKENS` / `AGENT_TEMPERATURE` | Параметры `chat()` |
| `AGENT_MODEL` | Явная модель; пусто = авто-выбор первой активной |

Потраченные токены считаются по `AgentMessage.tokens/cost`
(`agent_messages.cost_today()`). См. `docs/AGENT_TASKS.md` для общей картины очереди.

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