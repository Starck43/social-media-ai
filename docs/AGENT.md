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
| `app/agent/runtime.py` | Шагающий цикл агента: сессия, лимиты, подтверждения, loop |
| `app/agent/prompts.py` | Системный промпт + текст `/help` |
| `app/agent/session.py` | Загрузка/нормализация истории для LLM |
| `app/agent/tools.py` | Реестр инструментов, OpenAI-схемы, диспетчеризация |
| `app/agent/toolset/` | Реализации тулов: `system`, `collect`, `sources`, `schedule`, `reports` |
| `app/models/agent_session.py` | Одна строка на (channel, chat_id); volatile `state` |
| `app/models/agent_message.py` | Транскрипт диалога (user/assistant/tool) + токены/стоимость |
| `app/models/agent_memory.py` | Факты в постоянной памяти: (tenant, scope, key) -> value |

## Инструменты

Все инструменты зарегистрированы в `TOOL_REGISTRY` в `app/agent/tools.py`
(импорт `toolset` срабатывает по побочному эффекту). Схемы уходят в LLM как
OpenAI function calling.

Запись/отправка (`confirm=True`): `schedule_add/remove/pause`, `source_add/disable`,
`digest_send_now`. Их модель не выполняет сама — runtime кладёт вызов в
`session.state['pending_confirmation']` и ждёт явного «да»/«нет» от владельца.

Чтение/эйфемерные: `collect_now`, `sources_list`, `schedule_list`,
`report_period`, `system_status`, `memory_get/set`.

## Команды чата

- `/help` — список возможностей
- `/stop` — очистить историю (session остаётся)

## Память

`memory_set`/`memory_get` — durable KV, скоуп на workspace
(см. `agent_memory_manager`). Память **не** подмешивается в системный промпт
автоматически: модель вызывает `memory_get` сама, когда ей нужен факт.

## Лимиты и стоимость

| Параметр | Назначение |
|---|---|
| `AGENT_DAILY_COST_LIMIT` | USD-потолок агента за сегодня (UTC-день); проверка до и во время цикла |
| `tenant.daily_cost_limit` | Персональный лимит рабочего пространства (перекрывает глобальный) |
| `AGENT_MAX_ITERATIONS` | Макс. раундов tool-calls на одно сообщение |
| `AGENT_HISTORY_LIMIT` | Сколько сообщений истории уходит в LLM |
| `AGENT_MAX_TOKENS` / `AGENT_TEMPERATURE` | Параметры `chat()` |
| `AGENT_MODEL` | Явная модель; пусто = авто-выбор первой активной |

Потраченные токены/считаются по `AgentMessage.tokens/cost`
(`agent_messages.cost_today()`). См. `docs/SCHEDULER.md` для общей картины очереди.

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